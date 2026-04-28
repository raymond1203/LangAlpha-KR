"""PDF text extraction on raw bytes — pdfplumber primary, pypdf fallback.

Mirrors the internals of ``src/tools/crawler/extractors/pdf.py`` but takes
``bytes`` directly instead of a URL. The crawler's ``PdfExtractor`` downloads
the PDF over HTTP — wrong entry point for memo uploads where the bytes are
already in hand.

Extraction runs in a subprocess (spawn context) so a hung ``pdfplumber`` or
``pypdf`` call can be killed on timeout. The previous
``asyncio.wait_for(asyncio.to_thread(...))`` pattern only cancelled the
asyncio Future — the worker thread kept running with ~1 GB RSS, defeating the
``_EXTRACTION_CONCURRENCY`` cap once the semaphore released.
"""

from __future__ import annotations

import asyncio
import logging
import multiprocessing as mp
from collections.abc import Callable
from io import BytesIO
from multiprocessing.connection import Connection

from ptc_agent.agent.memo.schema import (
    MEMO_CONTENT_MIN_CHARS,
    MEMO_MAX_CONTENT_BYTES,
)

logger = logging.getLogger(__name__)

_EXTRACTION_TIMEOUT_S = 30

# Bound the per-PDF page count to keep extraction memory predictable. A 1000-page
# financial filing decompressed and held in memory comfortably crosses 1 GB on
# pdfplumber. 500 pages covers any realistic memo input; documents that exceed
# this almost always belong in a different workflow (e.g. summarized externally
# or chunked for retrieval) and should not blow up the FastAPI worker.
_MAX_PDF_PAGES = 500

# Cap on concurrent PDF extractions across the worker. pdfplumber holds the
# entire parsed page tree in memory until the ``with`` block exits — peak RSS
# per extraction can hit ~1 GB on the documented 500-page cap. Two concurrent
# extractions therefore bound peak memory at ~2 GB regardless of how many
# uploads land at once. Additional uploads queue on this semaphore.
_EXTRACTION_CONCURRENCY = 2
_EXTRACTION_SEM = asyncio.Semaphore(_EXTRACTION_CONCURRENCY)

# Spawn (not fork): FastAPI runs threads (uvicorn loop, logging, asyncio I/O)
# and fork+threads is unsafe — known to deadlock libssl, glibc allocator, etc.
# Spawn pays ~100 ms to boot a fresh interpreter, negligible against the
# multi-second extraction work.
_MP_CTX = mp.get_context("spawn")
# Grace window between SIGTERM and SIGKILL when killing a runaway subprocess.
_PROC_KILL_GRACE_S = 1.0


class MemoPdfExtractionError(Exception):
    """Raised when a PDF yields less than ``MEMO_CONTENT_MIN_CHARS`` of text.

    Typical cause: the PDF has no text layer (scanned document). Surface as
    HTTP 422 at the router so the user knows to run OCR first.
    """


class MemoPdfTooManyPagesError(MemoPdfExtractionError):
    """Raised when a PDF exceeds the configured page-count cap."""


def _pdfplumber_extract(content: bytes) -> str:
    import pdfplumber

    pages: list[str] = []
    accumulated_bytes = 0
    with pdfplumber.open(BytesIO(content)) as pdf:
        if len(pdf.pages) > _MAX_PDF_PAGES:
            raise MemoPdfTooManyPagesError(
                f"PDF has {len(pdf.pages)} pages; "
                f"max is {_MAX_PDF_PAGES}. Split the document or summarize first."
            )
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            page.close()
            if text.strip():
                pages.append(f"--- Page {i} ---\n\n{text}")
                # Short-circuit when accumulated text already exceeds the
                # store-side cap. Defends against PDFs whose page count is
                # below the cap but whose individual pages emit megabytes
                # of text via embedded fonts or repeated streams.
                accumulated_bytes += len(text.encode("utf-8"))
                if accumulated_bytes > MEMO_MAX_CONTENT_BYTES:
                    break
    return "\n\n".join(pages)


def _pypdf_extract(content: bytes) -> str:
    import pypdf

    reader = pypdf.PdfReader(BytesIO(content))
    if len(reader.pages) > _MAX_PDF_PAGES:
        raise MemoPdfTooManyPagesError(
            f"PDF has {len(reader.pages)} pages; "
            f"max is {_MAX_PDF_PAGES}. Split the document or summarize first."
        )
    pages: list[str] = []
    accumulated_bytes = 0
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        if text.strip():
            pages.append(f"--- Page {i} ---\n\n{text}")
            accumulated_bytes += len(text.encode("utf-8"))
            if accumulated_bytes > MEMO_MAX_CONTENT_BYTES:
                break
    return "\n\n".join(pages)


def _subprocess_entry(
    extractor: Callable[[bytes], str],
    content: bytes,
    conn: Connection,
) -> None:
    """Run ``extractor`` in the subprocess and ship the result back via Pipe.

    Catches ``BaseException`` so even ``MemoryError`` / ``KeyboardInterrupt``
    propagate to the parent instead of leaving it blocked on ``poll()``.
    """
    try:
        result = extractor(content)
        conn.send(("ok", result))
    except BaseException as exc:  # noqa: BLE001 — must round-trip every failure
        try:
            conn.send(("err", exc))
        except Exception:
            # Exception is not picklable — fall through; parent will see EOF
            # when conn closes and raise a generic extractor failure.
            pass
    finally:
        conn.close()


def _terminate(proc: mp.Process) -> None:
    """SIGTERM the worker, escalate to SIGKILL after the grace window."""
    if not proc.is_alive():
        return
    proc.terminate()
    proc.join(timeout=_PROC_KILL_GRACE_S)
    if proc.is_alive():
        proc.kill()
        proc.join()


def _run_in_subprocess_blocking(
    extractor: Callable[[bytes], str],
    content: bytes,
    timeout: float,
) -> str:
    """Spawn ``extractor`` in a fresh subprocess and wait up to ``timeout``.

    Returns the extractor's return value. On timeout, terminates the
    subprocess (SIGTERM, then SIGKILL after the grace window) and raises
    ``asyncio.TimeoutError``. Any exception raised inside the subprocess is
    pickled across the Pipe and re-raised here.

    Designed to be wrapped by ``asyncio.to_thread`` from the async layer —
    the in-process timeout (``timeout + _PROC_KILL_GRACE_S`` upper bound)
    means the wrapping thread always returns instead of leaking.
    """
    parent_conn, child_conn = _MP_CTX.Pipe(duplex=False)
    proc = _MP_CTX.Process(
        target=_subprocess_entry,
        args=(extractor, content, child_conn),
        daemon=True,
    )
    proc.start()
    # Close the child end in the parent: ensures recv() raises EOFError
    # promptly if the worker dies without sending anything (segfault, OOM
    # kill) instead of blocking until the next send.
    child_conn.close()
    try:
        if not parent_conn.poll(timeout):
            _terminate(proc)
            raise asyncio.TimeoutError(
                f"PDF extraction timed out after {timeout}s"
            )
        try:
            kind, payload = parent_conn.recv()
        except EOFError as exc:
            raise RuntimeError(
                "PDF extractor subprocess died without sending a result"
            ) from exc
        proc.join()
        if kind == "ok":
            return payload
        # Subprocess raised — re-raise the (pickled) exception in the parent.
        raise payload
    finally:
        parent_conn.close()
        _terminate(proc)


async def _run_extractor(
    extractor: Callable[[bytes], str], content: bytes
) -> str:
    """Async wrapper that runs the blocking subprocess runner off-loop."""
    return await asyncio.to_thread(
        _run_in_subprocess_blocking,
        extractor,
        content,
        _EXTRACTION_TIMEOUT_S,
    )


async def extract_pdf_text(content: bytes) -> str:
    """Extract readable text from PDF bytes.

    Tries pdfplumber first, falls back to pypdf. If neither yields at least
    ``MEMO_CONTENT_MIN_CHARS`` of non-whitespace text, raises
    ``MemoPdfExtractionError`` so the router can return 422 with a clear
    message.

    Concurrent extractions are bounded by ``_EXTRACTION_SEM`` so the FastAPI
    worker's RSS stays bounded even under a burst of PDF uploads.
    """
    async with _EXTRACTION_SEM:
        # Primary: pdfplumber
        try:
            text = await _run_extractor(_pdfplumber_extract, content)
            if _is_usable(text):
                return text
        except asyncio.TimeoutError:
            logger.warning(
                "memo pdf pdfplumber timed out (%ss)", _EXTRACTION_TIMEOUT_S,
            )
        except MemoPdfTooManyPagesError:
            # Page-count cap is not a fall-through: the same PDF will trip pypdf
            # too. Surface the specific error so the router returns the page-cap
            # message to the user instead of the generic "couldn't extract" one.
            raise
        except Exception as exc:
            logger.debug(
                "memo pdf pdfplumber failed: %s; falling back to pypdf", exc,
            )

        # Fallback: pypdf
        try:
            text = await _run_extractor(_pypdf_extract, content)
            if _is_usable(text):
                return text
        except asyncio.TimeoutError:
            logger.warning("memo pdf pypdf timed out (%ss)", _EXTRACTION_TIMEOUT_S)
        except MemoPdfTooManyPagesError:
            raise
        except Exception as exc:
            logger.debug("memo pdf pypdf failed: %s", exc)

    raise MemoPdfExtractionError(
        "Could not extract readable text from this PDF (it may be a scan). "
        "Try OCR first."
    )


def _is_usable(text: str | None) -> bool:
    return bool(text) and len(text.strip()) >= MEMO_CONTENT_MIN_CHARS

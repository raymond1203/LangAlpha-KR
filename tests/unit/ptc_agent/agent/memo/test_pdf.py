"""Unit tests for PDF text extraction with pdfplumber/pypdf fallback.

Extraction now runs in a subprocess (spawn) so a hung extractor can be
killed on timeout. Tests at the control-flow level mock ``_run_extractor``
and call the (mocked) extractor in-process — exercising the same async
fall-through logic without paying spawn cost or pickling. The subprocess
runner itself has its own end-to-end tests that spawn a real worker.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import patch

import pytest

from ptc_agent.agent.memo.pdf import (
    MemoPdfExtractionError,
    MemoPdfTooManyPagesError,
    _run_in_subprocess_blocking,
    extract_pdf_text,
)


async def _run_extractor_in_process(extractor, content):
    """Stub that calls the (mocked) extractor in-process to bypass spawn."""
    return extractor(content)


class TestExtractPdfText:
    """Control-flow tests: pdfplumber → pypdf fallback, error handling."""

    @pytest.mark.asyncio
    async def test_pdfplumber_success_returns_text(self):
        with patch(
            "ptc_agent.agent.memo.pdf._run_extractor",
            side_effect=_run_extractor_in_process,
        ), patch(
            "ptc_agent.agent.memo.pdf._pdfplumber_extract",
            return_value="This is a PDF with plenty of readable content spanning multiple lines.",
        ) as mock_plumber, patch(
            "ptc_agent.agent.memo.pdf._pypdf_extract"
        ) as mock_pypdf:
            text = await extract_pdf_text(b"%PDF-1.4 fake")
            assert "readable content" in text
            mock_plumber.assert_called_once()
            mock_pypdf.assert_not_called()

    @pytest.mark.asyncio
    async def test_pypdf_fallback_when_pdfplumber_returns_short(self):
        # pdfplumber returns < MEMO_CONTENT_MIN_CHARS (50) — should try pypdf.
        with patch(
            "ptc_agent.agent.memo.pdf._run_extractor",
            side_effect=_run_extractor_in_process,
        ), patch(
            "ptc_agent.agent.memo.pdf._pdfplumber_extract", return_value="tiny"
        ), patch(
            "ptc_agent.agent.memo.pdf._pypdf_extract",
            return_value="x" * 100,
        ) as mock_pypdf:
            text = await extract_pdf_text(b"%PDF-1.4 fake")
            assert len(text.strip()) >= 50
            mock_pypdf.assert_called_once()

    @pytest.mark.asyncio
    async def test_pypdf_fallback_when_pdfplumber_raises(self):
        with patch(
            "ptc_agent.agent.memo.pdf._run_extractor",
            side_effect=_run_extractor_in_process,
        ), patch(
            "ptc_agent.agent.memo.pdf._pdfplumber_extract",
            side_effect=RuntimeError("pdfplumber blew up"),
        ), patch(
            "ptc_agent.agent.memo.pdf._pypdf_extract",
            return_value="x" * 100,
        ) as mock_pypdf:
            text = await extract_pdf_text(b"%PDF-1.4 fake")
            assert text
            mock_pypdf.assert_called_once()

    @pytest.mark.asyncio
    async def test_both_empty_raises_extraction_error(self):
        with patch(
            "ptc_agent.agent.memo.pdf._run_extractor",
            side_effect=_run_extractor_in_process,
        ), patch(
            "ptc_agent.agent.memo.pdf._pdfplumber_extract", return_value=""
        ), patch(
            "ptc_agent.agent.memo.pdf._pypdf_extract", return_value=""
        ):
            with pytest.raises(MemoPdfExtractionError) as exc:
                await extract_pdf_text(b"%PDF-1.4 scan")
            assert "readable text" in str(exc.value).lower()

    @pytest.mark.asyncio
    async def test_both_below_minimum_raises(self):
        with patch(
            "ptc_agent.agent.memo.pdf._run_extractor",
            side_effect=_run_extractor_in_process,
        ), patch(
            "ptc_agent.agent.memo.pdf._pdfplumber_extract", return_value="tiny"
        ), patch(
            "ptc_agent.agent.memo.pdf._pypdf_extract", return_value="also tiny"
        ):
            with pytest.raises(MemoPdfExtractionError):
                await extract_pdf_text(b"%PDF-1.4 scan")

    @pytest.mark.asyncio
    async def test_both_raise_raises_extraction_error(self):
        with patch(
            "ptc_agent.agent.memo.pdf._run_extractor",
            side_effect=_run_extractor_in_process,
        ), patch(
            "ptc_agent.agent.memo.pdf._pdfplumber_extract",
            side_effect=RuntimeError("plumber"),
        ), patch(
            "ptc_agent.agent.memo.pdf._pypdf_extract",
            side_effect=RuntimeError("pypdf"),
        ):
            with pytest.raises(MemoPdfExtractionError):
                await extract_pdf_text(b"%PDF-1.4 broken")

    @pytest.mark.asyncio
    async def test_pdfplumber_timeout_falls_back_to_pypdf(self):
        async def timeout_then_succeed(extractor, content):
            # Timeout on the first call (pdfplumber), succeed on the second
            # (pypdf) by routing through the in-process stub.
            from ptc_agent.agent.memo import pdf as pdf_module
            if extractor is pdf_module._pdfplumber_extract:
                raise asyncio.TimeoutError()
            return extractor(content)

        with patch(
            "ptc_agent.agent.memo.pdf._run_extractor",
            side_effect=timeout_then_succeed,
        ), patch(
            "ptc_agent.agent.memo.pdf._pypdf_extract",
            return_value="x" * 100,
        ) as mock_pypdf:
            text = await extract_pdf_text(b"%PDF-1.4 slow")
            assert text
            mock_pypdf.assert_called_once()

    @pytest.mark.asyncio
    async def test_too_many_pages_does_not_fall_back(self):
        with patch(
            "ptc_agent.agent.memo.pdf._run_extractor",
            side_effect=_run_extractor_in_process,
        ), patch(
            "ptc_agent.agent.memo.pdf._pdfplumber_extract",
            side_effect=MemoPdfTooManyPagesError("PDF has 1000 pages; max is 500."),
        ), patch(
            "ptc_agent.agent.memo.pdf._pypdf_extract"
        ) as mock_pypdf:
            with pytest.raises(MemoPdfTooManyPagesError):
                await extract_pdf_text(b"%PDF-1.4 huge")
            mock_pypdf.assert_not_called()


# ---------------------------------------------------------------------------
# Subprocess runner — real spawn tests
#
# These exercise ``_run_in_subprocess_blocking`` end-to-end: a real subprocess
# is spawned, the extractor runs there, and the result (or exception) round-
# trips through the Pipe. Each test caps at ~5 s; a regression to the old
# in-thread pattern would either hang past the timeout (test fails on CI
# runtime) or leak threads.
# ---------------------------------------------------------------------------

# Module-level helpers — must be importable by the spawn'd subprocess.

def _ok_extractor(content: bytes) -> str:
    return f"got {len(content)} bytes"


def _failing_extractor(_content: bytes) -> str:
    raise MemoPdfTooManyPagesError("synthetic page-cap trip")


def _hanging_extractor(_content: bytes) -> str:
    # Sleeps far longer than any realistic timeout — the test passes only if
    # the parent kills the worker before this returns.
    time.sleep(60)
    return "should never get here"


def _exiting_extractor(_content: bytes) -> str:
    # ``os._exit`` bypasses Python cleanup — _subprocess_entry's finally never
    # runs, so the child closes its Pipe end only via process death (the OS
    # closing the fd). Exercises the EOF-from-dead-child path the parent
    # surfaces as RuntimeError.
    import os
    os._exit(1)
    return "unreachable"


class TestSubprocessRunner:
    def test_returns_extractor_result(self):
        result = _run_in_subprocess_blocking(_ok_extractor, b"hello", timeout=10.0)
        assert result == "got 5 bytes"

    def test_propagates_subprocess_exception(self):
        with pytest.raises(MemoPdfTooManyPagesError) as exc:
            _run_in_subprocess_blocking(_failing_extractor, b"x", timeout=10.0)
        assert "synthetic page-cap trip" in str(exc.value)

    def test_timeout_kills_worker_within_grace(self):
        """Hung extractor must be terminated, with parent returning promptly."""
        start = time.monotonic()
        with pytest.raises(asyncio.TimeoutError):
            _run_in_subprocess_blocking(_hanging_extractor, b"x", timeout=0.5)
        elapsed = time.monotonic() - start
        # Upper bound: timeout (0.5) + grace (1.0) + spawn overhead (~0.5).
        # Generous 5 s ceiling so CI scheduler hiccups don't false-fail; the
        # regression we'd catch is "the call hangs past 30 s".
        assert elapsed < 5.0, (
            f"timeout cleanup took {elapsed:.2f}s — subprocess may not have been killed"
        )

    def test_back_to_back_calls_after_timeout(self):
        """A timed-out call must not poison the subsequent call."""
        with pytest.raises(asyncio.TimeoutError):
            _run_in_subprocess_blocking(_hanging_extractor, b"x", timeout=0.3)
        # Next call should succeed cleanly with a fresh subprocess.
        result = _run_in_subprocess_blocking(_ok_extractor, b"hi", timeout=10.0)
        assert result == "got 2 bytes"

    def test_subprocess_dies_without_sending_raises_runtime_error(self):
        """``os._exit`` skips _subprocess_entry's finally; parent sees EOF."""
        with pytest.raises(RuntimeError) as exc:
            _run_in_subprocess_blocking(_exiting_extractor, b"x", timeout=10.0)
        msg = str(exc.value).lower()
        assert "subprocess" in msg and "without sending" in msg

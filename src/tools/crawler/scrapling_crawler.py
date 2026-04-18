"""
Crawler backend using Scrapling library.

Implements a three-tier fetching strategy:
  Tier 1 (Fast):    AsyncFetcher.get() -- HTTP-only, TLS impersonation
  Tier 2 (Dynamic): AsyncDynamicSession -- Playwright/patchright Chromium
  Tier 3 (Stealth): AsyncStealthySession -- Camoufox anti-bot bypass

Automatic fallback: Tier 1 -> Tier 2 -> Tier 3

Browser lifecycle: Tier 2/3 use the session classes directly (rather than the
`DynamicFetcher.async_fetch()` classmethod wrapper) so we can shield
`session.close()` from cancellation. `asyncio.wait_for` in safe_wrapper.py
cancels the fetch coroutine on timeout; if close() is not shielded, it gets
cancelled mid-teardown and orphans Chromium helper processes. See fix plan
2026-04-18.
"""

import asyncio
import logging

import html2text

from .backend import CrawlOutput

logger = logging.getLogger(__name__)

# Signals that indicate Tier 1 content is blocked/empty and needs browser rendering
_BLOCKED_SIGNALS = [
    "cloudflare",
    "just a moment",
    "checking your browser",
    "enable javascript",
    "please enable js",
    "ray id",
    "access denied",
    "403 forbidden",
    "captcha",
]


def _log_close_task_exception(task: asyncio.Task) -> None:
    # Observes close_task after outer cancel so asyncio doesn't emit
    # "Task exception was never retrieved" when close() raises post-cancel.
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning(f"Browser session close failed post-cancel: {exc!r}")


def _needs_browser(html_body: str, status: int) -> bool:
    """Detect if HTTP-only fetch returned blocked/empty content."""
    if status >= 400:
        return True
    if not html_body or len(html_body.strip()) < 200:
        return True
    lower = html_body.lower()
    return any(signal in lower for signal in _BLOCKED_SIGNALS)


def _needs_stealth(html_body: str, status: int) -> bool:
    """Detect if dynamic fetch hit anti-bot protection."""
    if status in (401, 403):
        return True
    lower = (html_body or "").lower()
    # Cloudflare challenge
    if "cloudflare" in lower and ("ray id" in lower or "just a moment" in lower):
        return True
    # DataDome / generic anti-bot challenge (short page with JS challenge)
    if len(lower) < 2000 and ("enable js" in lower or "enable javascript" in lower):
        return True
    return False


def _html_to_markdown(html: str) -> str:
    """Convert HTML to clean markdown using html2text."""
    converter = html2text.HTML2Text()
    converter.ignore_links = False
    converter.ignore_images = False
    converter.body_width = 0  # No wrapping
    converter.ignore_emphasis = False
    return converter.handle(html)


def _extract_title(page) -> str:
    """Extract page title from Scrapling response."""
    try:
        title_el = page.css("title::text")
        return title_el.get() or ""
    except Exception:
        return ""


class ScraplingCrawler:
    """
    Async crawler using Scrapling with tiered fetching.

    Satisfies the CrawlerBackend protocol.
    """

    def __init__(
        self,
        timeout: int = 30000,
        disable_resources: bool = True,
        network_idle: bool = True,
    ):
        self.timeout = timeout
        self.disable_resources = disable_resources
        self.network_idle = network_idle

    async def crawl(self, url: str) -> str:
        """Crawl and return markdown."""
        output = await self.crawl_with_metadata(url)
        return output.markdown

    async def crawl_with_metadata(self, url: str) -> CrawlOutput:
        """Crawl with tiered fallback, return CrawlOutput."""
        from .extractors.base import _validate_url
        _validate_url(url)

        # --- Tier 1: Fast HTTP fetch (requires curl_cffi) ---
        try:
            page, html_body, status = await self._tier1_fetch(url)
            if not _needs_browser(html_body, status):
                title = _extract_title(page)
                markdown = _html_to_markdown(html_body)
                logger.debug(f"Tier 1 (fast) succeeded for {url}")
                return CrawlOutput(title=title, html=html_body, markdown=markdown)
            logger.debug(f"Tier 1 insufficient for {url}, escalating to Tier 2")
        except ImportError:
            # curl_cffi not installed — skip Tier 1 (scrapling without [fetchers])
            logger.debug(f"Tier 1 unavailable (curl_cffi not installed), using Tier 2 for {url}")
        except Exception as e:
            logger.debug(f"Tier 1 failed for {url}: {e}, escalating to Tier 2")

        # --- Tier 2: Dynamic browser fetch ---
        try:
            page, html_body, status = await self._tier2_fetch(url)
            if not _needs_stealth(html_body, status):
                title = _extract_title(page)
                markdown = _html_to_markdown(html_body)
                logger.debug(f"Tier 2 (dynamic) succeeded for {url}")
                return CrawlOutput(title=title, html=html_body, markdown=markdown)
            logger.debug(f"Tier 2 blocked for {url}, escalating to Tier 3")
        except Exception as e:
            logger.debug(f"Tier 2 failed for {url}: {e}, escalating to Tier 3")

        # --- Tier 3: Stealth fetch ---
        try:
            page, html_body, status = await self._tier3_fetch(url)
            if _needs_stealth(html_body, status):
                logger.debug(f"Tier 3 still blocked for {url} (status={status})")
                return CrawlOutput(title="", html="", markdown="")
            title = _extract_title(page)
            markdown = _html_to_markdown(html_body)
            logger.debug(f"Tier 3 (stealth) completed for {url} (status={status})")
            return CrawlOutput(title=title, html=html_body, markdown=markdown)
        except Exception as e:
            logger.debug(f"Tier 3 failed for {url}: {e}")
            return CrawlOutput(title="", html="", markdown="")

    async def _tier1_fetch(self, url: str):
        from scrapling.fetchers import AsyncFetcher

        page = await AsyncFetcher.get(
            url,
            stealthy_headers=True,
            follow_redirects=True,
            timeout=self.timeout / 1000,  # ms → seconds (curl_cffi convention)
        )
        html_body = page.body.decode(page.encoding or "utf-8", errors="replace")
        return page, html_body, page.status

    async def _tier2_fetch(self, url: str):
        # Direct session use (not DynamicFetcher.async_fetch) so we own the
        # close() path and can shield it from outer cancellation.
        from scrapling.engines._browsers._controllers import AsyncDynamicSession

        session = AsyncDynamicSession(
            headless=True,
            disable_resources=self.disable_resources,
            network_idle=self.network_idle,
            timeout=self.timeout,
        )
        return await self._fetch_with_session(session, url)

    async def _tier3_fetch(self, url: str):
        from scrapling.engines._browsers._stealth import AsyncStealthySession

        session = AsyncStealthySession(
            headless=True,
            network_idle=self.network_idle,
            timeout=self.timeout,
        )
        return await self._fetch_with_session(session, url, solve_cloudflare=True)

    async def _fetch_with_session(self, session, url: str, **fetch_kwargs):
        """Start a scrapling session, fetch one URL, shield close() from cancel.

        Prior learnings applied:
          - CancelledError inherits from BaseException and is NOT caught by
            `except Exception`. It must be handled explicitly if we want to
            run teardown before re-raising.
          - `asyncio.shield(coro)` only protects a Task; wrapping a bare
            coroutine is a no-op. We create the close task explicitly, then
            await shield() so the inner task keeps running even if the outer
            is cancelled.
          - Scrapling's `AsyncDynamicSession.start()` wraps browser spawn in
            `except Exception`, which misses CancelledError. On cancellation
            during start(), `self.playwright` stays set but `_is_alive=False`,
            and close() early-returns on the `_is_alive` guard. We force
            `_is_alive=True` before close() so the cleanup path actually runs
            and stops the playwright driver. Without this, cancel-during-start
            leaks the node driver process.
        """
        try:
            await session.start()
            page = await session.fetch(url, **fetch_kwargs)
            html_body = page.body.decode(page.encoding or "utf-8", errors="replace")
            return page, html_body, page.status
        finally:
            # If start() was cancelled mid-spawn, scrapling's own cleanup was
            # skipped (CancelledError bypassed its except Exception). Force
            # close() to run its teardown branches — they're idempotent on
            # None-valued context/browser, so this is safe even if only
            # playwright.stop() is needed.
            if (
                getattr(session, "playwright", None) is not None
                and not getattr(session, "_is_alive", True)
            ):
                session._is_alive = True  # unblock close()'s guard clause
            close_task = asyncio.create_task(session.close())
            try:
                await asyncio.shield(close_task)
            except asyncio.CancelledError:
                # Outer task cancelled. close_task survives (shield) and will
                # complete in the background, freeing the browser. Attach a
                # done-callback so its exception (if any) is logged instead of
                # surfacing as asyncio's "Task exception was never retrieved".
                close_task.add_done_callback(_log_close_task_exception)
                pass
            except Exception as e:
                # tini (init: true in compose) reaps leaked helpers in prod;
                # dev/macOS has no init backstop so a failed close leaks until
                # the Python process exits.
                logger.warning(
                    f"Browser session close failed (init will reap if present): {e}"
                )

    async def shutdown(self) -> None:
        """No persistent resources to clean up (sessions are per-fetch)."""
        pass

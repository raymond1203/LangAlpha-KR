"""
Safe wrapper for crawler operations with isolation and fault tolerance.

Provides defense-in-depth protection:
- Circuit breaker for fault tolerance (fail fast after repeated failures)
- Timeouts for all operations (prevent hanging)
- Resource limiting (queue size, concurrency)
- Auto browser reset on circuit open
- Graceful degradation (never crash, always return structured errors)

Backend-agnostic: pluggable via CrawlerBackend protocol,
selectable via `crawler.backend` in agent_config.yaml.
"""

import asyncio
import logging
import os
import signal
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Callable
from enum import Enum

logger = logging.getLogger(__name__)

# Orphan reaper tunables. Tier 2 (patchright) spawns `chrome*`; Tier 3 (Camoufox)
# spawns `firefox`/`camoufox`. Both get reparented to PID 1 on cancellation, so
# the reaper matches both prefixes.
_INIT_PID = 1
_BROWSER_COMM_PREFIXES = ("chrome", "firefox", "camoufox")
_FORK_EXEC_GRACE_SECONDS = 5.0  # Skip procs briefly showing ppid=1 during fork/exec
_STAT_PPID_IDX = 1       # /proc/[pid]/stat field 4 (1-indexed), 0-indexed after comm
_STAT_STARTTIME_IDX = 19  # /proc/[pid]/stat field 22, 0-indexed after comm
# The reaper only runs when PID 1 is one of these, because ppid==1 only reliably
# means "orphan reaped to init" when init actually reaps. If PID 1 is our python
# worker (init: true silently failed), ppid==1 matches direct children including
# LIVE browsers of in-flight workflows — the reaper would then SIGKILL healthy crawls.
# `docker-init` is Docker's bundled tini wrapper; observed as PID 1 under
# `init: true` on Docker Desktop and Docker CE without explicit tini install.
_SAFE_INIT_PROCESSES = ("tini", "docker-init", "catatonit", "dumb-init")


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing fast, rejecting requests
    HALF_OPEN = "half_open" # Testing recovery


@dataclass
class CrawlResult:
    """Structured crawl result that never raises exceptions."""
    success: bool
    markdown: Optional[str] = None
    title: Optional[str] = None
    error: Optional[str] = None
    error_type: Optional[str] = None  # timeout, circuit_open, queue_full, browser_error, etc.


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


class QueueFullError(Exception):
    """Raised when crawler queue is at capacity."""
    pass


class CrawlerCircuitBreaker:
    """
    Circuit breaker for crawler operations.

    State machine:
    - CLOSED: Normal operation, tracking failures
    - OPEN: After failure_threshold failures, reject all requests immediately
    - HALF_OPEN: After recovery_timeout, allow one request to test recovery

    When circuit opens, triggers browser reset to recover from corrupted state.
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
    ):
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds before transitioning from OPEN to HALF_OPEN
            success_threshold: Number of successes in HALF_OPEN to close circuit
        """
        self.failure_threshold = failure_threshold
        self._base_recovery_timeout = recovery_timeout
        self._max_recovery_timeout = 900.0  # 15 minutes cap
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self._consecutive_opens = 0
        self.last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()

    async def check_state(self) -> None:
        """Check and potentially transition state based on time elapsed."""
        async with self._lock:
            if self.state == CircuitState.OPEN:
                if self.last_failure_time and \
                   time.time() - self.last_failure_time > self.recovery_timeout:
                    logger.info("Circuit breaker transitioning to half-open")
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0

    async def record_success(self) -> None:
        """Record successful operation."""
        async with self._lock:
            self.failure_count = 0
            if self.state == CircuitState.HALF_OPEN:
                self.success_count += 1
                if self.success_count >= self.success_threshold:
                    logger.info("Circuit breaker closing after recovery")
                    self.state = CircuitState.CLOSED
                    self._consecutive_opens = 0
                    self.recovery_timeout = self._base_recovery_timeout

    async def record_failure(self, trigger_reset: Optional[Callable] = None) -> None:
        """
        Record failed operation.

        Args:
            trigger_reset: Optional async callback to reset browser when circuit opens
        """
        async with self._lock:
            self.failure_count += 1
            self.last_failure_time = time.time()
            should_open = False

            if self.state == CircuitState.HALF_OPEN:
                self._consecutive_opens += 1
                self.recovery_timeout = min(
                    self._base_recovery_timeout * (2 ** self._consecutive_opens),
                    self._max_recovery_timeout,
                )
                logger.warning(
                    f"Circuit breaker re-opening after half-open failure "
                    f"(consecutive_opens={self._consecutive_opens}, "
                    f"next_recovery={self.recovery_timeout}s)"
                )
                self.state = CircuitState.OPEN
                should_open = True
            elif self.failure_count >= self.failure_threshold:
                logger.warning(f"Circuit breaker opening after {self.failure_count} failures")
                self.state = CircuitState.OPEN
                should_open = True

            # Trigger browser reset when circuit opens
            if should_open and trigger_reset:
                logger.info("Triggering browser reset due to circuit open")
                asyncio.create_task(trigger_reset())

    def is_open(self) -> bool:
        """Check if circuit is open (rejecting requests)."""
        return self.state == CircuitState.OPEN


_VALID_BACKENDS = frozenset({"scrapling", "router"})


class SafeCrawlerWrapper:
    """
    Safe wrapper for web crawling with comprehensive fault tolerance.

    Features:
    - Circuit breaker: Fail fast after repeated failures
    - Timeouts: All operations have guaranteed timeouts
    - Resource limiting: Queue size and concurrency limits
    - Auto recovery: Browser reset when circuit opens
    - Graceful degradation: Never raises, always returns CrawlResult
    - Backend-agnostic: pluggable via CrawlerBackend protocol
    """

    def __init__(
        self,
        max_concurrent: int = 10,
        max_queue_size: int = 100,
        default_timeout: float = 60.0,
        slot_timeout: float = 10.0,
        circuit_failure_threshold: int = 5,
        circuit_recovery_timeout: float = 60.0,
        circuit_success_threshold: int = 2,
        backend: str = "scrapling",
    ):
        """
        Initialize safe crawler wrapper.

        Args:
            max_concurrent: Maximum number of concurrent crawls
            max_queue_size: Maximum number of queued requests
            default_timeout: Default timeout for crawl operations (seconds)
            slot_timeout: Timeout for waiting for a crawl slot (seconds)
            circuit_failure_threshold: Failures before opening circuit
            circuit_recovery_timeout: Seconds before testing recovery
            circuit_success_threshold: Successes to close circuit
            backend: Crawler backend (default: "scrapling")
        """
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._queue_count = 0
        self._max_queue = max_queue_size
        self._default_timeout = default_timeout
        self._slot_timeout = slot_timeout
        self._circuit = CrawlerCircuitBreaker(
            failure_threshold=circuit_failure_threshold,
            recovery_timeout=circuit_recovery_timeout,
            success_threshold=circuit_success_threshold,
        )
        self._lock = asyncio.Lock()
        # Serialize reaper runs: circuit-open can trigger many in parallel and a
        # single /proc walk suffices; N concurrent walks are thundering-herd waste
        # and widen the PID-reuse window between stat and os.kill.
        self._reaper_lock = asyncio.Lock()
        self._crawler = None  # Lazy-initialized
        if backend not in _VALID_BACKENDS:
            raise ValueError(f"Unknown crawler backend: {backend!r}. Must be one of {_VALID_BACKENDS}")
        self._backend = backend

    async def _get_crawler(self):
        """Lazy-initialize crawler based on configured backend."""
        if self._crawler is None:
            if self._backend == "scrapling":
                from .scrapling_crawler import ScraplingCrawler
                self._crawler = ScraplingCrawler()
            elif self._backend == "router":
                from .router import ContentRouter
                self._crawler = ContentRouter()
            else:
                raise ValueError(f"Unknown crawler backend: {self._backend}")
        return self._crawler

    async def _trigger_browser_reset(self) -> None:
        """Reap orphaned browser processes when the circuit opens.

        Belt-and-suspenders to Tier 1 (tini via `init: true` in compose): if a
        browser fetch was cancelled before scrapling's session.close() ran, its
        child Chromium/Camoufox/Firefox processes get reparented to PID 1. tini
        reaps them eventually, but when the circuit trips we actively free RAM +
        PID slots now instead of waiting.

        Runs the /proc walk in a thread so walking thousands of /proc/*/stat
        entries does not block the event loop while SSE streams are in flight.
        A lock serializes concurrent circuit-open events onto one walk at a time.
        """
        if self._reaper_lock.locked():
            logger.debug("Browser reaper already running; skipping concurrent invocation")
            return
        async with self._reaper_lock:
            try:
                await asyncio.to_thread(self._reap_orphan_browsers_sync)
            except Exception as e:
                logger.warning(
                    f"Browser reset reaper failed (non-Linux? readonly cgroup?): {e}"
                )

    @staticmethod
    def _reap_orphan_browsers_sync() -> None:
        """Sync /proc walk. Runs in a thread via `_trigger_browser_reset`."""
        proc_root = Path("/proc")
        if not proc_root.exists():
            logger.debug("Browser reset skipped: /proc not available (not Linux)")
            return

        # Abort if PID 1 is not an init process — in that case ppid==1 may
        # indicate a LIVE child of our python worker, not a reaped orphan.
        try:
            pid1_comm = (proc_root / "1" / "comm").read_text().strip()
        except (OSError, IndexError):
            logger.warning("Browser reset aborted: could not read /proc/1/comm")
            return
        if pid1_comm not in _SAFE_INIT_PROCESSES:
            logger.error(
                f"Browser reset aborted: PID 1 is {pid1_comm!r}, not an init "
                "process. Reaping would risk killing live browsers. Verify "
                "`init: true` in docker-compose and tini is installed."
            )
            return

        try:
            uptime = float((proc_root / "uptime").read_text().split()[0])
            clk_tck = os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError) as e:
            logger.warning(f"Browser reset: could not read uptime/CLK_TCK: {e}")
            return

        killed = 0
        for pid_dir in proc_root.iterdir():
            if not pid_dir.name.isdigit():
                continue
            try:
                # /proc/[pid]/stat: "pid (comm) state ppid ... starttime ...".
                # comm is inside parens and can contain spaces, so rsplit(')', 1).
                stat_fields = (pid_dir / "stat").read_text().rsplit(")", 1)
                comm = stat_fields[0].split("(", 1)[1]
                rest = stat_fields[1].split()
                ppid = int(rest[_STAT_PPID_IDX])
                start_ticks = int(rest[_STAT_STARTTIME_IDX])
                if ppid != _INIT_PID or not comm.startswith(_BROWSER_COMM_PREFIXES):
                    continue
                age_s = uptime - (start_ticks / clk_tck)
                if age_s < _FORK_EXEC_GRACE_SECONDS:
                    continue
                os.kill(int(pid_dir.name), signal.SIGKILL)
                killed += 1
            except (ProcessLookupError, FileNotFoundError, PermissionError, IndexError, ValueError):
                # Process died mid-scan, stat fields malformed, or PID recycled — expected race.
                continue

        if killed > 0:
            logger.warning(
                f"Circuit reset reaped {killed} orphaned browser processes"
            )

    async def crawl(
        self,
        url: str,
        timeout: Optional[float] = None,
    ) -> CrawlResult:
        """
        Safely crawl a URL with all protections.

        This method never raises exceptions - it always returns a CrawlResult
        with success=True/False and appropriate error information.

        Args:
            url: URL to crawl
            timeout: Optional timeout override (seconds)

        Returns:
            CrawlResult with success status and content/error
        """
        timeout = timeout or self._default_timeout

        # Check circuit breaker state
        await self._circuit.check_state()
        if self._circuit.is_open():
            return CrawlResult(
                success=False,
                error="Crawler temporarily unavailable (circuit open)",
                error_type="circuit_open",
            )

        # Check queue capacity
        async with self._lock:
            if self._queue_count >= self._max_queue:
                return CrawlResult(
                    success=False,
                    error="Crawler queue at capacity",
                    error_type="queue_full",
                )
            self._queue_count += 1

        try:
            # Acquire semaphore with timeout
            try:
                await asyncio.wait_for(
                    self._semaphore.acquire(),
                    timeout=self._slot_timeout,
                )
            except asyncio.TimeoutError:
                return CrawlResult(
                    success=False,
                    error=f"Timeout waiting for crawler slot ({self._slot_timeout}s)",
                    error_type="queue_timeout",
                )

            try:
                # Execute crawl with timeout
                crawler = await self._get_crawler()
                output = await asyncio.wait_for(
                    crawler.crawl_with_metadata(url),
                    timeout=timeout,
                )

                # Treat empty/near-empty content as a failed crawl —
                # prevents blocked/errored pages from counting as circuit successes
                if not output.markdown or len(output.markdown.strip()) < 10:
                    await self._circuit.record_failure(self._trigger_browser_reset)
                    return CrawlResult(
                        success=False,
                        markdown=output.markdown,
                        title=output.title,
                        error="Page returned empty content",
                        error_type="empty_content",
                    )

                await self._circuit.record_success()
                return CrawlResult(
                    success=True,
                    markdown=output.markdown,
                    title=output.title,
                )

            except asyncio.TimeoutError:
                await self._circuit.record_failure(self._trigger_browser_reset)
                return CrawlResult(
                    success=False,
                    error=f"Crawl timed out after {timeout}s",
                    error_type="timeout",
                )
            except asyncio.CancelledError:
                # Don't count cancellation as failure
                return CrawlResult(
                    success=False,
                    error="Crawl was cancelled",
                    error_type="cancelled",
                )
            except Exception as e:
                await self._circuit.record_failure(self._trigger_browser_reset)
                error_str = str(e)

                # Classify error type for better diagnostics
                if "has been closed" in error_str or "Target page" in error_str:
                    error_type = "browser_closed"
                elif "ERR_NAME_NOT_RESOLVED" in error_str:
                    error_type = "dns_error"
                elif "ERR_CONNECTION_REFUSED" in error_str:
                    error_type = "connection_refused"
                elif "ERR_CONNECTION_TIMED_OUT" in error_str:
                    error_type = "connection_timeout"
                elif "net::" in error_str:
                    error_type = "network_error"
                else:
                    error_type = "crawl_error"

                return CrawlResult(
                    success=False,
                    error=error_str[:200],  # Truncate long errors
                    error_type=error_type,
                )
            finally:
                self._semaphore.release()
        finally:
            async with self._lock:
                self._queue_count -= 1

    def get_status(self) -> dict:
        """
        Get wrapper status for monitoring.

        Returns:
            Dict with circuit state, failure count, queue info
        """
        return {
            "circuit_state": self._circuit.state.value,
            "failure_count": self._circuit.failure_count,
            "success_count": self._circuit.success_count,
            "consecutive_opens": self._circuit._consecutive_opens,
            "recovery_timeout": self._circuit.recovery_timeout,
            "queue_count": self._queue_count,
            "max_queue": self._max_queue,
            "last_failure_time": self._circuit.last_failure_time,
        }

    def is_healthy(self) -> bool:
        """Check if crawler is healthy (circuit not open)."""
        return not self._circuit.is_open()


# Global singleton instance
_safe_wrapper: Optional[SafeCrawlerWrapper] = None
_wrapper_lock = asyncio.Lock()


def _build_configured_wrapper() -> SafeCrawlerWrapper:
    """Build a SafeCrawlerWrapper from agent_config settings."""
    try:
        from src.config.tool_settings import (
            get_crawler_max_concurrent,
            get_crawler_page_timeout,
            get_crawler_queue_max_size,
            get_crawler_queue_slot_timeout,
            get_crawler_circuit_failure_threshold,
            get_crawler_circuit_recovery_timeout,
            get_crawler_circuit_success_threshold,
            get_crawler_backend,
        )

        return SafeCrawlerWrapper(
            max_concurrent=get_crawler_max_concurrent(),
            default_timeout=get_crawler_page_timeout() / 1000,
            max_queue_size=get_crawler_queue_max_size(),
            slot_timeout=get_crawler_queue_slot_timeout(),
            circuit_failure_threshold=get_crawler_circuit_failure_threshold(),
            circuit_recovery_timeout=get_crawler_circuit_recovery_timeout(),
            circuit_success_threshold=get_crawler_circuit_success_threshold(),
            backend=get_crawler_backend(),
        )
    except Exception as e:
        logger.warning(f"Failed to load crawler config, using defaults: {e}")
        return SafeCrawlerWrapper()


async def get_safe_crawler() -> SafeCrawlerWrapper:
    """
    Get or create safe crawler wrapper singleton.

    Loads configuration from agent_config.yaml via tool_settings helpers.
    """
    global _safe_wrapper

    if _safe_wrapper is not None:
        return _safe_wrapper

    async with _wrapper_lock:
        # Double-check after acquiring lock
        if _safe_wrapper is not None:
            return _safe_wrapper

        _safe_wrapper = _build_configured_wrapper()
        return _safe_wrapper


def get_safe_crawler_sync() -> SafeCrawlerWrapper:
    """
    Synchronous version of get_safe_crawler for non-async contexts.

    Note: This creates the wrapper with default config if not already initialized.
    """
    global _safe_wrapper

    if _safe_wrapper is None:
        _safe_wrapper = _build_configured_wrapper()

    return _safe_wrapper

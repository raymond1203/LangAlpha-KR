"""Intraday OHLCV caching with envelope metadata and incremental delta refresh.

Key improvements over the previous flat-TTL, full-refetch approach:
- **Envelope** wraps bars with watermark / complete / market_phase / fetched_at.
- **Interval-aware TTL** (e.g. 5 s for 1 s bars, 90 s for 1 min bars).
- **Delta refresh** fetches only bars from the watermark onward, then merges.
- **Market hours gating** skips refresh when market is closed.
- **Date-free cache keys** for live queries enable cross-request sharing.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from src.config.settings import get_ohlcv_ttl
from src.data_client import get_market_data_provider
from src.server.services.cache._ohlcv_envelope import (
    _EMPTY_RESULT_TTL,
    _build_envelope,
    _merge_bars,
    _needs_refresh,
    _parse_envelope,
)
from src.utils.cache.redis_cache import get_cache_client
from src.utils.market_hours import current_market_phase, seconds_until_next_open

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache key builder
# ---------------------------------------------------------------------------

class IntradayCacheKeyBuilder:
    """Build cache keys for intraday OHLCV data.

    Live queries (to_date is None or today) omit dates so that requests for
    different date ranges can share the same cached envelope.  Historical
    queries (to_date strictly in the past) embed both dates and get a long TTL.
    """

    PREFIX = "ohlcv"

    @classmethod
    def _is_live(cls, to_date: Optional[str]) -> bool:
        if to_date is None:
            return True
        try:
            return to_date >= date.today().strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return True

    @classmethod
    def stock_key(
        cls,
        symbol: str,
        interval: str = "1min",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        source: Optional[str] = None,
    ) -> str:
        symbol = symbol.upper()
        src = f"{source}:" if source else ""
        if cls._is_live(to_date):
            return f"{cls.PREFIX}:{src}stock:{symbol}:{interval}"
        return f"{cls.PREFIX}:{src}stock:{symbol}:{interval}:{from_date}:{to_date}"

    @classmethod
    def index_key(
        cls,
        symbol: str,
        interval: str = "1min",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        source: Optional[str] = None,
    ) -> str:
        normalized = symbol.lstrip("^").upper()
        src = f"{source}:" if source else ""
        if cls._is_live(to_date):
            return f"{cls.PREFIX}:{src}index:{normalized}:{interval}"
        return f"{cls.PREFIX}:{src}index:{normalized}:{interval}:{from_date}:{to_date}"


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class IntradayFetchResult:
    """Result of an intraday data fetch operation."""

    symbol: str
    interval: str
    data: List[Dict[str, Any]]
    cached: bool
    ttl_remaining: Optional[int]
    background_refresh_triggered: bool
    cache_key: Optional[str] = None
    watermark: Optional[str] = None
    complete: Optional[bool] = None
    market_phase: Optional[str] = None
    error: Optional[str] = None


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class IntradayCacheService:
    """Singleton service for cached intraday OHLCV data with delta refresh."""

    _instance: Optional["IntradayCacheService"] = None
    _refresh_locks: Dict[str, asyncio.Lock]
    _max_concurrent_fetches: int = 10

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._refresh_locks = {}
            cls._instance._semaphore = asyncio.Semaphore(cls._max_concurrent_fetches)
        return cls._instance

    @classmethod
    def get_instance(cls) -> "IntradayCacheService":
        return cls()

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def _ttl_for(interval: str) -> int:
        return get_ohlcv_ttl(interval)

    @staticmethod
    def _effective_ttl(base_ttl: int, complete: bool) -> int:
        """Extend TTL when market is closed so the key survives until next open."""
        if complete:
            secs = seconds_until_next_open()
            return max(base_ttl, secs) if secs > 0 else base_ttl
        return base_ttl

    def _get_refresh_lock(self, cache_key: str) -> asyncio.Lock:
        if cache_key not in self._refresh_locks:
            self._refresh_locks[cache_key] = asyncio.Lock()
        return self._refresh_locks[cache_key]

    async def _fetch_data(
        self,
        symbol: str,
        is_index: bool,
        interval: str = "1min",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Fetch intraday data and return ``(bars, source_name)``."""
        provider = await get_market_data_provider()
        data, source = await provider.get_intraday_with_source(
            symbol=symbol,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
            is_index=is_index,
            user_id=user_id,
        )
        return data, source

    # -- delta refresh ----------------------------------------------------

    async def _delta_refresh(
        self,
        cache_key: str,
        symbol: str,
        is_index: bool,
        interval: str,
        user_id: Optional[str] = None,
    ) -> None:
        """Background delta refresh: fetch only from watermark onward, merge."""
        lock = self._get_refresh_lock(cache_key)
        if lock.locked():
            logger.debug(f"Delta refresh already in progress for {cache_key}")
            return

        async with lock:
            try:
                cache = get_cache_client()

                # Re-read envelope (may have been updated by another refresh)
                raw = await cache.get(cache_key)
                envelope = _parse_envelope(raw) if raw else None

                phase = current_market_phase()
                closed = phase == "closed"

                if envelope and envelope.get("complete") and closed:
                    # Still closed — nothing to do
                    return

                watermark = envelope["watermark"] if envelope else None
                existing_bars = envelope["bars"] if envelope else []

                # Determine from_date for delta fetch
                # Use the date portion of the watermark (YYYY-MM-DD prefix)
                delta_from = watermark[:10] if watermark and len(watermark) >= 10 else None

                delta, _source = await self._fetch_data(
                    symbol, is_index, interval,
                    from_date=delta_from,
                    to_date=None,
                    user_id=user_id,
                )

                if watermark and existing_bars:
                    merged = _merge_bars(existing_bars, delta, watermark)
                else:
                    merged = delta

                # Build new envelope
                complete = closed and len(merged) > 0
                base_ttl = self._ttl_for(interval)
                effective = self._effective_ttl(base_ttl, complete)
                new_envelope = _build_envelope(merged, phase, complete, stored_ttl=effective)

                await cache.set(cache_key, new_envelope, ttl=effective)

                delta_count = len(delta)
                total_count = len(merged)
                logger.debug(
                    f"Delta refresh for {cache_key}: "
                    f"fetched {delta_count} bars, total {total_count}, "
                    f"phase={phase}, complete={complete}"
                )

            except Exception as e:
                logger.warning(f"Delta refresh failed for {cache_key}: {e}")

    # -- public API -------------------------------------------------------

    async def get_stock_intraday(
        self,
        symbol: str,
        interval: str = "1min",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> IntradayFetchResult:
        return await self._get_intraday(
            symbol=symbol, is_index=False,
            interval=interval, from_date=from_date, to_date=to_date,
            user_id=user_id,
        )

    async def get_index_intraday(
        self,
        symbol: str,
        interval: str = "1min",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> IntradayFetchResult:
        return await self._get_intraday(
            symbol=symbol, is_index=True,
            interval=interval, from_date=from_date, to_date=to_date,
            user_id=user_id,
        )

    def _build_key(
        self, symbol: str, is_index: bool, interval: str,
        from_date: Optional[str], to_date: Optional[str],
        source: Optional[str] = None,
    ) -> str:
        if is_index:
            return IntradayCacheKeyBuilder.index_key(symbol, interval, from_date, to_date, source=source)
        return IntradayCacheKeyBuilder.stock_key(symbol, interval, from_date, to_date, source=source)

    async def _find_cached(
        self, symbol: str, is_index: bool, interval: str,
        from_date: Optional[str], to_date: Optional[str],
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        """Try cache lookup across all known data sources.

        Returns ``(cache_key, envelope)`` on hit, ``(None, None)`` on miss.
        """
        cache = get_cache_client()
        provider = await get_market_data_provider()
        for source in provider.source_names:
            key = self._build_key(symbol, is_index, interval, from_date, to_date, source=source)
            raw = await cache.get(key)
            envelope = _parse_envelope(raw) if raw else None
            if envelope is not None:
                return key, envelope
        return None, None

    async def _get_intraday(
        self,
        symbol: str,
        is_index: bool,
        interval: str = "1min",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> IntradayFetchResult:
        normalized = symbol.lstrip("^").upper()

        base_ttl = self._ttl_for(interval)
        cache = get_cache_client()
        phase = current_market_phase()

        # --- Try cache (across all known sources) ---
        cache_key, envelope = await self._find_cached(
            normalized, is_index, interval, from_date, to_date,
        )

        if envelope is not None:
            bars = envelope["bars"]
            watermark = envelope.get("watermark")
            complete = envelope.get("complete", False)
            stored_ttl = envelope.get("stored_ttl", 0)
            elapsed = time.time() - envelope.get("fetched_at", 0)
            ttl_remaining = max(0, int(stored_ttl - elapsed)) if stored_ttl else None

            bg_triggered = False
            if _needs_refresh(envelope, base_ttl):
                bg_triggered = True
                asyncio.create_task(
                    self._delta_refresh(cache_key, normalized, is_index, interval, user_id)
                )

            return IntradayFetchResult(
                symbol=normalized,
                interval=interval,
                data=bars,
                cached=True,
                ttl_remaining=ttl_remaining,
                background_refresh_triggered=bg_triggered,
                cache_key=cache_key,
                watermark=watermark,
                complete=complete,
                market_phase=phase,
            )

        # --- Cache miss: full fetch ---
        try:
            data, source = await self._fetch_data(
                normalized, is_index, interval, from_date, to_date, user_id=user_id,
            )
            cache_key = self._build_key(normalized, is_index, interval, from_date, to_date, source=source)

            closed = phase == "closed"
            complete = closed and len(data) > 0
            effective_ttl = self._effective_ttl(base_ttl, complete)

            # Use short TTL for empty results so we retry quickly
            if not data:
                effective_ttl = _EMPTY_RESULT_TTL
            new_envelope = _build_envelope(data, phase, complete, stored_ttl=effective_ttl)

            await cache.set(cache_key, new_envelope, ttl=effective_ttl)

            return IntradayFetchResult(
                symbol=normalized,
                interval=interval,
                data=data,
                cached=False,
                ttl_remaining=effective_ttl,
                background_refresh_triggered=False,
                cache_key=cache_key,
                watermark=new_envelope["watermark"],
                complete=complete,
                market_phase=phase,
            )

        except Exception as e:
            logger.error(f"Failed to fetch intraday data for {symbol}: {e}")
            return IntradayFetchResult(
                symbol=normalized,
                interval=interval,
                data=[],
                cached=False,
                ttl_remaining=None,
                background_refresh_triggered=False,
                market_phase=phase,
                error=str(e),
            )

    # -- batch API --------------------------------------------------------

    async def get_batch_stocks(
        self,
        symbols: List[str],
        interval: str = "1min",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str], Dict[str, Any]]:
        return await self._get_batch(
            symbols=symbols, is_index=False,
            interval=interval, from_date=from_date, to_date=to_date,
            user_id=user_id,
        )

    async def get_batch_indexes(
        self,
        symbols: List[str],
        interval: str = "1min",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str], Dict[str, Any]]:
        return await self._get_batch(
            symbols=symbols, is_index=True,
            interval=interval, from_date=from_date, to_date=to_date,
            user_id=user_id,
        )

    async def _get_batch(
        self,
        symbols: List[str],
        is_index: bool,
        interval: str = "1min",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str], Dict[str, Any]]:
        """Two-phase batch: parallel cache lookups, then semaphore-controlled API calls."""
        results: Dict[str, List[Dict[str, Any]]] = {}
        errors: Dict[str, str] = {}
        cache_hits = 0
        background_refreshes = 0

        base_ttl = self._ttl_for(interval)
        cache = get_cache_client()
        phase = current_market_phase()

        # Phase 1: parallel cache lookups (try all source-namespaced keys)
        cache_misses: List[str] = []
        # cache_key resolved per symbol (set during hit or fetch)
        resolved_keys: Dict[str, str] = {}

        async def check_cache(sym: str) -> None:
            nonlocal cache_hits, background_refreshes
            normalized = sym.lstrip("^").upper()

            key, envelope = await self._find_cached(
                normalized, is_index, interval, from_date, to_date,
            )

            if envelope is not None:
                results[normalized] = envelope["bars"]
                resolved_keys[sym] = key
                cache_hits += 1
                if _needs_refresh(envelope, base_ttl):
                    background_refreshes += 1
                    asyncio.create_task(
                        self._delta_refresh(key, normalized, is_index, interval, user_id)
                    )
            else:
                cache_misses.append(sym)

        await asyncio.gather(*[check_cache(s) for s in symbols])

        # Phase 2: fetch misses with semaphore
        if cache_misses:
            provider = await get_market_data_provider()

            async def fetch_from_api(sym: str) -> None:
                normalized = sym.lstrip("^").upper()
                async with self._semaphore:
                    try:
                        data, source = await provider.get_intraday_with_source(
                            symbol=normalized, interval=interval,
                            from_date=from_date, to_date=to_date,
                            is_index=is_index, user_id=user_id,
                        )
                        results[normalized] = data
                        key = self._build_key(
                            normalized, is_index, interval, from_date, to_date, source=source,
                        )

                        closed = phase == "closed"
                        complete = closed
                        eff_ttl = self._effective_ttl(base_ttl, complete)
                        if not data:
                            eff_ttl = _EMPTY_RESULT_TTL
                        env = _build_envelope(data, phase, complete, stored_ttl=eff_ttl)
                        asyncio.create_task(cache.set(key, env, ttl=eff_ttl))

                    except Exception as e:
                        logger.error(f"Failed to fetch {sym}: {e}")
                        errors[sym.lstrip("^").upper()] = str(e)

            await asyncio.gather(*[fetch_from_api(s) for s in cache_misses])

        cache_stats = {
            "total_requests": len(symbols),
            "cache_hits": cache_hits,
            "cache_misses": len(cache_misses),
            "background_refreshes": background_refreshes,
        }
        return results, errors, cache_stats

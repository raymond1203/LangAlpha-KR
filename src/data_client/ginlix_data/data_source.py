"""ginlix-data implementation of MarketDataSource."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from .client import GinlixDataClient

logger = logging.getLogger(__name__)

# FMP-style interval → ginlix-data (timespan, multiplier)
INTERVAL_MAP: dict[str, tuple[str, int]] = {
    "1s": ("second", 1),
    "1min": ("minute", 1),
    "5min": ("minute", 5),
    "15min": ("minute", 15),
    "30min": ("minute", 30),
    "1hour": ("hour", 1),
    "4hour": ("hour", 4),
}

# Yahoo Finance / FMP symbol
_INDEX_SYMBOL_MAP: dict[str, str] = {
    "GSPC": "I:SPX",
    "DJI": "I:DJI",
    "IXIC": "I:COMP",
    "RUT": "I:RUT",
    "VIX": "I:VIX",
    "NDX": "I:NDX",
}


class GinlixDataSource:
    """Market data source backed by ginlix-data REST API."""

    # Interval-aware lookback windows (trading days).
    # Each live cache key stores bars from this window; incoming requests
    # with a from/to that falls within the window are served from cache.
    # Per-interval limit overrides for get_aggregates (default=5000).
    _LIMIT_BY_INTERVAL: dict[str, int] = {
        "1s": 25000,  # full trading day ~23,400 bars
    }
    _DEFAULT_LIMIT = 5000

    _LOOKBACK_BY_INTERVAL: dict[str, int] = {
        "1s": 3,       # 3 days to cover Friday from Sunday
        "1min": 5,     # ~1,950 bars, ~230 KB
        "5min": 10,    # ~780 bars, ~95 KB
        "15min": 10,   # ~260 bars, ~32 KB
        "30min": 10,   # ~130 bars, ~16 KB
        "1hour": 10,   # ~65 bars, ~8 KB
        "4hour": 10,   # ~17 bars, ~2 KB
    }
    _DAILY_LOOKBACK_DAYS = 365 * 2  # ~504 bars, ~55 KB

    def __init__(self, client: GinlixDataClient) -> None:
        self.client = client

    @classmethod
    def lookback_days_for(cls, interval: str) -> int:
        """Return the default lookback window in calendar days for *interval*."""
        return cls._LOOKBACK_BY_INTERVAL.get(interval, 7)

    @staticmethod
    def _index_symbol(symbol: str) -> str:
        """Convert a Yahoo/FMP-style index symbol to ginlix-data format."""
        if symbol.startswith("I:"):
            return symbol
        bare = symbol.lstrip("^").upper()
        return _INDEX_SYMBOL_MAP.get(bare, f"I:{bare}")

    @staticmethod
    def _default_dates(
        from_date: str | None, to_date: str | None, lookback_days: int
    ) -> tuple[str, str]:
        """ginlix-data requires from/to — supply sensible defaults."""
        today = date.today()
        if to_date is None:
            to_date = today.strftime("%Y-%m-%d")
        if from_date is None:
            from_date = (today - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
        return from_date, to_date

    async def get_intraday(
        self,
        symbol: str,
        interval: str,
        from_date: str | None = None,
        to_date: str | None = None,
        is_index: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        market = "index" if is_index else "stock"
        api_symbol = self._index_symbol(symbol) if is_index else symbol
        timespan, multiplier = INTERVAL_MAP.get(interval, ("minute", 1))
        lookback = self._LOOKBACK_BY_INTERVAL.get(interval, 7)
        from_date, to_date = self._default_dates(from_date, to_date, lookback)
        limit = self._LIMIT_BY_INTERVAL.get(interval, self._DEFAULT_LIMIT)
        raw = await self.client.get_aggregates(
            market=market,
            symbol=api_symbol,
            timespan=timespan,
            multiplier=multiplier,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
            user_id=user_id,
        )
        return [self._normalize(r) for r in raw]

    async def get_daily(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        from_date, to_date = self._default_dates(
            from_date, to_date, self._DAILY_LOOKBACK_DAYS
        )
        raw = await self.client.get_aggregates(
            market="stock",
            symbol=symbol,
            timespan="day",
            multiplier=1,
            from_date=from_date,
            to_date=to_date,
            user_id=user_id,
        )
        return [self._normalize(r) for r in raw]

    @staticmethod
    def _normalize(row: dict[str, Any]) -> dict[str, Any]:
        """Normalize a ginlix-data bar to the standard OHLCV shape."""
        return {
            "date": row.get("date", ""),
            "open": row.get("open", 0.0),
            "high": row.get("high", 0.0),
            "low": row.get("low", 0.0),
            "close": row.get("close", 0.0),
            "volume": int(row.get("volume", 0)),
        }

    async def close(self) -> None:
        await self.client.close()


# Backward-compatible alias
GinlixDataPriceProvider = GinlixDataSource

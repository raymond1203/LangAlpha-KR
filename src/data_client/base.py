"""Abstract market data source protocol.

All OHLCV data sources (FMP, ginlix-data) implement this protocol
so that cache services and routes are backend-agnostic.
"""

from __future__ import annotations

from typing import Any, Protocol


class MarketDataSource(Protocol):
    """Unified interface for OHLCV price data fetching."""

    async def get_intraday(
        self,
        symbol: str,
        interval: str,
        from_date: str | None = None,
        to_date: str | None = None,
        is_index: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return intraday OHLCV bars.

        Each dict has: ``{date, open, high, low, close, volume}``.
        *user_id* is forwarded to the upstream service for access-control.
        """
        ...

    async def get_daily(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return daily OHLCV bars.

        Each dict has: ``{date, open, high, low, close, volume}``.
        *user_id* is forwarded to the upstream service for access-control.
        """
        ...

    async def close(self) -> None:
        """Release resources held by the data source."""
        ...


# Backward-compatible alias
PriceDataProvider = MarketDataSource

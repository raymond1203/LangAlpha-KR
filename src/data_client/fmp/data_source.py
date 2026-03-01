"""FMP implementation of MarketDataSource.

Thin wrapper around :class:`FMPClient` that conforms to the
:class:`~src.data_client.base.MarketDataSource` protocol.
"""

from __future__ import annotations

from typing import Any

from .fmp_client import FMPClient


class FMPDataSource:
    """Market data source backed by Financial Modeling Prep."""

    # FMP supports these intraday intervals; anything else should be rejected
    # so the chain can fall through to a provider that does support it.
    _SUPPORTED_INTERVALS = frozenset({"1min", "5min", "15min", "30min", "1hour", "4hour"})

    async def get_intraday(
        self,
        symbol: str,
        interval: str,
        from_date: str | None = None,
        to_date: str | None = None,
        is_index: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if interval not in self._SUPPORTED_INTERVALS:
            raise ValueError(
                f"Interval '{interval}' is not supported by this data source"
            )
        api_symbol = f"^{symbol}" if is_index and not symbol.startswith("^") else symbol
        async with FMPClient() as client:
            data = await client.get_intraday_chart(
                symbol=api_symbol,
                interval=interval,
                from_date=from_date,
                to_date=to_date,
            )
        return data or []

    async def get_daily(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        async with FMPClient() as client:
            data = await client.get_stock_price(
                symbol=symbol,
                from_date=from_date,
                to_date=to_date,
            )
        return data or []

    async def close(self) -> None:
        pass  # FMPClient manages its own lifecycle per-request


# Backward-compatible alias
FMPPriceProvider = FMPDataSource

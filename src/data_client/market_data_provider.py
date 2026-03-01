"""Composite market data provider with chain-of-responsibility fallback.

Wraps multiple :class:`MarketDataSource` implementations and routes
requests based on symbol market region, falling back to the next
provider on error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .base import MarketDataSource

logger = logging.getLogger(__name__)

# Symbol suffix → market region
_SUFFIX_MAP: dict[str, str] = {
    "HK": "hk",
    "SS": "cn",
    "SZ": "cn",
    "L": "uk",
    "T": "jp",
    "TO": "ca",
    "AX": "au",
    "PA": "eu",
    "DE": "eu",
    "AS": "eu",
    "MI": "eu",
    "MC": "eu",
    "SW": "eu",
    "KS": "kr",
    "KQ": "kr",
    "TW": "tw",
    "SI": "sg",
    "BO": "in",
    "NS": "in",
}


def symbol_market(symbol: str) -> str:
    """Derive market region from a symbol's suffix.

    Bare symbols (no dot) and ``.US`` suffixes are treated as US.
    """
    if "." not in symbol or symbol.endswith(".US"):
        return "us"
    suffix = symbol.rsplit(".", 1)[-1].upper()
    return _SUFFIX_MAP.get(suffix, "other")


@dataclass
class ProviderEntry:
    name: str
    source: MarketDataSource
    markets: set[str] = field(default_factory=lambda: {"all"})


class MarketDataProvider:
    """Chain-of-responsibility provider implementing :class:`MarketDataSource`.

    Iterates over an ordered list of ``ProviderEntry`` items.  For each
    request the chain is filtered to entries whose ``markets`` set contains
    ``"all"`` or the symbol's derived market region.  On failure the next
    candidate is tried.
    """

    def __init__(self, entries: list[ProviderEntry]) -> None:
        self.entries = entries

    def _sources_for(self, symbol: str) -> list[ProviderEntry]:
        """Return entries that cover *symbol*'s market, in priority order."""
        market = symbol_market(symbol)
        return [e for e in self.entries if "all" in e.markets or market in e.markets]

    async def _try_chain(self, method: str, symbol: str, **kwargs: Any) -> Any:
        data, _ = await self._try_chain_with_source(method, symbol, **kwargs)
        return data

    async def _try_chain_with_source(
        self, method: str, symbol: str, **kwargs: Any
    ) -> tuple[Any, str]:
        """Like ``_try_chain`` but also returns the winning source name."""
        candidates = self._sources_for(symbol)
        if not candidates:
            raise RuntimeError(f"No data source configured for market of {symbol}")
        last_exc: Exception | None = None
        for entry in candidates:
            try:
                result = await getattr(entry.source, method)(symbol=symbol, **kwargs)
                return result, entry.name
            except Exception as exc:
                logger.warning(
                    "market_data.fallback | source=%s symbol=%s error=%s",
                    entry.name,
                    symbol,
                    exc,
                )
                last_exc = exc
        raise last_exc  # type: ignore[misc]

    # -- MarketDataSource interface ------------------------------------------

    async def get_intraday(
        self,
        symbol: str,
        interval: str,
        from_date: str | None = None,
        to_date: str | None = None,
        is_index: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._try_chain(
            "get_intraday",
            symbol,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
            is_index=is_index,
            user_id=user_id,
        )

    async def get_intraday_with_source(
        self,
        symbol: str,
        interval: str,
        from_date: str | None = None,
        to_date: str | None = None,
        is_index: bool = False,
        user_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """Like ``get_intraday`` but also returns the source name."""
        return await self._try_chain_with_source(
            "get_intraday",
            symbol,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
            is_index=is_index,
            user_id=user_id,
        )

    async def get_daily(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._try_chain(
            "get_daily",
            symbol,
            from_date=from_date,
            to_date=to_date,
            user_id=user_id,
        )

    async def get_daily_with_source(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
        user_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """Like ``get_daily`` but also returns the source name."""
        return await self._try_chain_with_source(
            "get_daily",
            symbol,
            from_date=from_date,
            to_date=to_date,
            user_id=user_id,
        )

    async def close(self) -> None:
        """Close all underlying sources, catching errors independently."""
        for entry in self.entries:
            try:
                await entry.source.close()
            except Exception:
                logger.warning("market_data.close | source=%s failed", entry.name, exc_info=True)

    @property
    def source_names(self) -> list[str]:
        return [e.name for e in self.entries]

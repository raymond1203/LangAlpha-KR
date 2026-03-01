"""Data access layer.

This package is the single source of truth for fetching raw financial data.

Design goals:
- Unified API for both host tools and sandbox code.
- Backend can be either direct-provider (e.g. FMP) or MCP-based.
- Do not inline secrets into sandbox-uploaded code.

MCP convention:
- The price data MCP server should be named `price_data`.
  When running inside a PTC sandbox, this will be available as `tools.price_data`.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
import importlib
from typing import Any

from .fmp import FMPClient
from .base import MarketDataSource, PriceDataProvider  # noqa: F401 — re-export alias

logger = logging.getLogger(__name__)

MCP_SERVER_NAME = "price_data"

# ---------------------------------------------------------------------------
# Source registry — maps config name → (availability_check, async_constructor)
# ---------------------------------------------------------------------------


def _ginlix_data_available() -> bool:
    from src.config.settings import GINLIX_DATA_URL
    return bool(GINLIX_DATA_URL)


def _fmp_available() -> bool:
    return bool(os.getenv("FMP_API_KEY"))


async def _build_ginlix_data_source() -> MarketDataSource:
    from .ginlix_data import get_ginlix_data_client
    from .ginlix_data.data_source import GinlixDataSource
    client = await get_ginlix_data_client()
    return GinlixDataSource(client)


async def _build_fmp_source() -> MarketDataSource:
    from .fmp.data_source import FMPDataSource
    return FMPDataSource()


_SOURCE_REGISTRY: dict[str, tuple[Any, Any]] = {
    "ginlix-data": (_ginlix_data_available, _build_ginlix_data_source),
    "fmp": (_fmp_available, _build_fmp_source),
}

# ---------------------------------------------------------------------------
# Market data provider factory
# ---------------------------------------------------------------------------

_market_data_provider: MarketDataSource | None = None
_market_data_provider_lock = asyncio.Lock()


async def get_market_data_provider() -> MarketDataSource:
    """Return the active :class:`MarketDataSource` singleton.

    Builds an ordered chain from ``market_data.providers`` in config.yaml.
    Each provider that passes its availability check is included.
    When multiple sources are available, requests are routed by market
    region with automatic fallback.
    """
    global _market_data_provider
    if _market_data_provider is not None:
        return _market_data_provider

    async with _market_data_provider_lock:
        if _market_data_provider is not None:
            return _market_data_provider

        from src.config.settings import get_market_data_providers
        from .market_data_provider import MarketDataProvider, ProviderEntry

        provider_configs = get_market_data_providers()
        entries: list[ProviderEntry] = []

        for cfg in provider_configs:
            name = cfg["name"]
            markets = set(cfg.get("markets", ["all"]))
            reg = _SOURCE_REGISTRY.get(name)
            if reg and reg[0]():  # availability check
                source = await reg[1]()
                entries.append(ProviderEntry(name=name, source=source, markets=markets))
                logger.info("market_data.source.registered | name=%s markets=%s", name, markets)
            else:
                logger.info("market_data.source.skipped | name=%s (unavailable)", name)

        if not entries:
            raise RuntimeError("No market data source available — check config and credentials")

        _market_data_provider = MarketDataProvider(entries)

        return _market_data_provider


# Backward-compatible alias
get_price_provider = get_market_data_provider


class FinancialDataBackendError(RuntimeError):
    """Raised when no backend is available for a request."""


@dataclass(frozen=True)
class FinancialDataResult:
    """Standard wrapper for raw financial-data results."""

    data: Any
    source: str  # "mcp" | "direct"


def _try_import_mcp_module() -> Any | None:
    """Best-effort import of the sandbox-generated MCP module."""

    try:
        return importlib.import_module(f"tools.{MCP_SERVER_NAME}")
    except Exception:
        return None


async def _direct_get_stock_data(
    symbol: str,
    interval: str = "1day",
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict[str, Any]]:
    interval_lower = interval.lower()

    # Default date window for intraday queries (FMP requires dates)
    if interval_lower not in {"1day", "daily", "1d", "day"}:
        if end_date is None:
            end_date = date.today().strftime("%Y-%m-%d")
        if start_date is None:
            start_date = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")

    async with FMPClient() as client:
        if interval_lower in {"1day", "daily", "1d", "day"}:
            rows = await client.get_stock_price(symbol, from_date=start_date, to_date=end_date)
        else:
            rows = await client.get_intraday_chart(symbol, interval_lower, from_date=start_date, to_date=end_date)

    return rows or []


async def get_stock_data(
    symbol: str,
    interval: str = "1day",
    start_date: str | None = None,
    end_date: str | None = None,
) -> FinancialDataResult:
    """Unified OHLCV fetch.

    Returns raw rows (list of dicts). In sandbox, prefers MCP (`tools.price_data`).
    In host-only mode, falls back to direct provider access.
    """

    mcp_module = _try_import_mcp_module()
    if mcp_module is not None and hasattr(mcp_module, "get_stock_data"):
        # MCP tool modules are generated as sync functions.
        data = mcp_module.get_stock_data(
            symbol=symbol,
            interval=interval,
            start_date=start_date,
            end_date=end_date,
        )
        return FinancialDataResult(data=data, source="mcp")

    data = await _direct_get_stock_data(symbol, interval, start_date, end_date)
    return FinancialDataResult(data=data, source="direct")


async def get_quote(symbol: str) -> FinancialDataResult:
    """Get real-time quote (raw provider response)."""

    mcp_module = _try_import_mcp_module()
    if mcp_module is not None and hasattr(mcp_module, "get_quote"):
        data = mcp_module.get_quote(symbol=symbol)
        return FinancialDataResult(data=data, source="mcp")

    async with FMPClient() as client:
        data = await client.get_quote(symbol)
    return FinancialDataResult(data=data, source="direct")


async def get_profile(symbol: str) -> FinancialDataResult:
    """Get company profile (raw provider response)."""

    mcp_module = _try_import_mcp_module()
    if mcp_module is not None and hasattr(mcp_module, "get_profile"):
        data = mcp_module.get_profile(symbol=symbol)
        return FinancialDataResult(data=data, source="mcp")

    async with FMPClient() as client:
        data = await client.get_profile(symbol)
    return FinancialDataResult(data=data, source="direct")


async def _direct_get_asset_data(
    symbol: str,
    asset_type: str,
    interval: str = "daily",
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    at = asset_type.lower().strip()
    interval_lower = interval.lower()

    if at not in {"stock", "commodity", "crypto", "forex"}:
        raise ValueError("Invalid asset_type. Must be one of: stock, commodity, crypto, forex")

    # Default date window for intraday queries (FMP requires dates)
    if interval_lower not in {"1day", "daily", "1d", "day"}:
        if to_date is None:
            to_date = date.today().strftime("%Y-%m-%d")
        if from_date is None:
            from_date = (date.today() - timedelta(days=7)).strftime("%Y-%m-%d")

    async with FMPClient() as client:
        if at == "stock":
            if interval_lower in {"1day", "daily", "1d", "day"}:
                rows = await client.get_stock_price(symbol, from_date=from_date, to_date=to_date)
            else:
                rows = await client.get_intraday_chart(symbol, interval_lower, from_date=from_date, to_date=to_date)
            return rows or []

        # commodity/crypto/forex
        if interval_lower in {"1day", "daily", "1d", "day"}:
            if at == "commodity":
                rows = await client.get_commodity_price(symbol, from_date=from_date, to_date=to_date)
            elif at == "crypto":
                rows = await client.get_crypto_price(symbol, from_date=from_date, to_date=to_date)
            else:
                rows = await client.get_forex_price(symbol, from_date=from_date, to_date=to_date)
            return rows or []

        if interval_lower not in {"1min", "5min", "1hour"}:
            raise ValueError("Unsupported interval for commodity/crypto/forex")

        if at == "commodity":
            rows = await client.get_commodity_intraday_chart(symbol, interval_lower, from_date=from_date, to_date=to_date)
        elif at == "crypto":
            rows = await client.get_crypto_intraday_chart(symbol, interval_lower, from_date=from_date, to_date=to_date)
        else:
            rows = await client.get_forex_intraday_chart(symbol, interval_lower, from_date=from_date, to_date=to_date)

    return rows or []


async def get_asset_data(
    symbol: str,
    asset_type: str,
    interval: str = "daily",
    from_date: str | None = None,
    to_date: str | None = None,
) -> FinancialDataResult:
    """Unified OHLCV fetch for stock/commodity/crypto/forex.

    In sandbox, prefers MCP (`tools.price_data.get_asset_data`).
    """

    mcp_module = _try_import_mcp_module()
    if mcp_module is not None and hasattr(mcp_module, "get_asset_data"):
        data = mcp_module.get_asset_data(
            symbol=symbol,
            asset_type=asset_type,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
        )
        return FinancialDataResult(data=data, source="mcp")

    data = await _direct_get_asset_data(symbol, asset_type, interval, from_date, to_date)
    return FinancialDataResult(data=data, source="direct")

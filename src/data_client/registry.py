"""Provider singletons and source registry.

Builds market-data, news, and financial-data provider singletons from
config + credentials.  All three use double-checked locking via
``asyncio.Lock`` to avoid redundant initialization.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from .base import (
    FinancialDataSource,
    MarketDataSource,
    MarketIntelSource,
    NewsDataSource,
)
from .financial_data_provider import FinancialDataProvider

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Availability checks
# ---------------------------------------------------------------------------


def _ginlix_data_available() -> bool:
    from src.config.settings import GINLIX_DATA_URL

    return bool(GINLIX_DATA_URL)


def _fmp_available() -> bool:
    return bool(os.getenv("FMP_API_KEY"))


def _yfinance_available() -> bool:
    try:
        import yfinance  # noqa: F401

        return True
    except ImportError:
        return False


# FORK: 한국 시장 데이터 소스
def _korean_available() -> bool:
    try:
        import pykrx  # noqa: F401

        return True
    except ImportError:
        return False


# FORK: 한국 뉴스 소스 — RSS 기반이라 httpx 만 있으면 동작 (pykrx 와 독립).
# httpx / defusedxml 은 pyproject.toml 의 hard dep 이라 import 가 실제로 실패할 일은 없지만,
# 다른 _available 함수들과 패턴 일관성 + 미래의 dep 변동 (예: 다른 라이브러리로 교체) 시 안전망용.
def _korean_news_available() -> bool:
    try:
        import defusedxml  # noqa: F401
        import httpx  # noqa: F401

        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# Async source constructors
# ---------------------------------------------------------------------------


async def _build_ginlix_data_source() -> MarketDataSource:
    from .ginlix_data import get_ginlix_data_client
    from .ginlix_data.data_source import GinlixDataSource

    client = await get_ginlix_data_client()
    return GinlixDataSource(client)


async def _build_fmp_source() -> MarketDataSource:
    from .fmp.data_source import FMPDataSource

    return FMPDataSource()


async def _build_ginlix_data_news_source() -> NewsDataSource:
    from .ginlix_data import get_ginlix_data_client
    from .ginlix_data.news_source import GinlixDataNewsSource

    client = await get_ginlix_data_client()
    return GinlixDataNewsSource(client)


async def _build_fmp_news_source() -> NewsDataSource:
    from .fmp.news_source import FMPNewsSource

    return FMPNewsSource()


async def _build_yfinance_source() -> MarketDataSource:
    from .yfinance.data_source import YFinanceDataSource

    return YFinanceDataSource()


# FORK: 한국 시장 데이터 소스
async def _build_korean_source() -> MarketDataSource:
    from .korean.data_source import KoreanDataSource

    return KoreanDataSource()


async def _build_yfinance_news_source() -> NewsDataSource:
    from .yfinance.news_source import YFinanceNewsSource

    return YFinanceNewsSource()


# FORK: 한국 뉴스 소스 (매경/연합 RSS 어그리게이터)
async def _build_korean_news_source() -> NewsDataSource:
    from .korean.news_source import KoreanNewsSource

    return KoreanNewsSource()


# ---------------------------------------------------------------------------
# Source registries — map config name → (availability_check, async_constructor)
# ---------------------------------------------------------------------------

_SOURCE_REGISTRY: dict[str, tuple[Any, Any]] = {
    "ginlix-data": (_ginlix_data_available, _build_ginlix_data_source),
    "fmp": (_fmp_available, _build_fmp_source),
    "yfinance": (_yfinance_available, _build_yfinance_source),
    "korean": (_korean_available, _build_korean_source),  # FORK: 한국 시장 (pykrx)
}

_NEWS_SOURCE_REGISTRY: dict[str, tuple[Any, Any]] = {
    "ginlix-data": (_ginlix_data_available, _build_ginlix_data_news_source),
    "fmp": (_fmp_available, _build_fmp_news_source),
    "yfinance": (_yfinance_available, _build_yfinance_news_source),
    "korean": (_korean_news_available, _build_korean_news_source),  # FORK: 한국 RSS
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
            # FORK: get_news_data_provider 와 동일하게 lowercase 정규화 — region/market 비교가 모두 lowercase 기준
            markets = {m.lower() for m in cfg.get("markets", ["all"])}
            reg = _SOURCE_REGISTRY.get(name)
            if reg and reg[0]():  # availability check
                source = await reg[1]()
                entries.append(ProviderEntry(name=name, source=source, markets=markets))
                logger.debug(
                    "market_data.source.registered | name=%s markets=%s", name, markets
                )
            else:
                logger.debug("market_data.source.skipped | name=%s (unavailable)", name)

        if not entries:
            raise RuntimeError(
                "No market data source available — check config and credentials"
            )

        _market_data_provider = MarketDataProvider(entries)

        return _market_data_provider


# Backward-compatible alias
get_price_provider = get_market_data_provider

# ---------------------------------------------------------------------------
# News data provider factory
# ---------------------------------------------------------------------------

_news_data_provider = None
_news_data_provider_lock = asyncio.Lock()


async def get_news_data_provider():
    """Return the active :class:`NewsDataProvider` singleton.

    Builds an ordered chain from ``news_data.providers`` in config.yaml.
    """
    global _news_data_provider
    if _news_data_provider is not None:
        return _news_data_provider

    async with _news_data_provider_lock:
        if _news_data_provider is not None:
            return _news_data_provider

        from src.config.settings import get_news_data_providers
        from .news_data_provider import NewsDataProvider

        provider_configs = get_news_data_providers()
        sources: list[tuple[str, Any, set[str]]] = []

        for cfg in provider_configs:
            name = cfg["name"]
            markets = {m.lower() for m in cfg.get("markets", ["all"])}
            reg = _NEWS_SOURCE_REGISTRY.get(name)
            if reg and reg[0]():  # availability check
                source = await reg[1]()
                sources.append((name, source, markets))
                logger.debug(
                    "news_data.source.registered | name=%s markets=%s", name, markets
                )
            else:
                logger.debug("news_data.source.skipped | name=%s (unavailable)", name)

        if not sources:
            raise RuntimeError(
                "No news data source available — check config and credentials"
            )

        _news_data_provider = NewsDataProvider(sources)
        return _news_data_provider


# ---------------------------------------------------------------------------
# Financial data provider factory
# ---------------------------------------------------------------------------

_financial_data_provider: FinancialDataProvider | None = None
_financial_data_provider_lock = asyncio.Lock()


async def get_financial_data_provider() -> FinancialDataProvider:
    """Return the active :class:`FinancialDataProvider` singleton.

    Builds the composite from available backends:
    - :class:`FMPFinancialSource` if ``FMP_API_KEY`` is set.
    - :class:`GinlixMarketIntelSource` if ``GINLIX_DATA_URL`` is configured.
    """
    global _financial_data_provider
    if _financial_data_provider is not None:
        return _financial_data_provider

    async with _financial_data_provider_lock:
        if _financial_data_provider is not None:
            return _financial_data_provider

        financial: FinancialDataSource | None = None
        intel: MarketIntelSource | None = None

        if _fmp_available():
            from .fmp import get_fmp_client
            from .fmp.financial_source import FMPFinancialSource

            fmp_client = await get_fmp_client()
            financial = FMPFinancialSource(fmp_client)
            logger.debug(
                "financial_data.source.registered | name=fmp (FinancialDataSource)"
            )
        elif _yfinance_available():
            from .yfinance.financial_source import YFinanceFinancialSource

            financial = YFinanceFinancialSource()
            logger.debug(
                "financial_data.source.registered | name=yfinance (FinancialDataSource)"
            )

        if _ginlix_data_available():
            from .ginlix_data import get_ginlix_data_client
            from .ginlix_data.market_intel_source import GinlixMarketIntelSource

            client = await get_ginlix_data_client()
            intel = GinlixMarketIntelSource(client)
            logger.debug(
                "financial_data.source.registered | name=ginlix-data (MarketIntelSource)"
            )

        _financial_data_provider = FinancialDataProvider(
            financial=financial, intel=intel
        )
        return _financial_data_provider

"""
Tests for /analyst-data 미지원 응답 + /overview KR router-level 통합.

- /analyst-data 는 KR ticker 호출 시 200 with unsupported=True (helper 직접 호출).
- /overview KR 분기는 #42 에서 KoreanFundamentalsSource 호출 — router 레벨로
  AsyncClient + ASGITransport 통합 검증 (FastAPI dependency / response 직렬화 포함).
- KoreanFundamentalsSource 자체 단위는 test_fundamentals_source.py 참조.
"""

from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from src.server.app.market_data import (
    get_analyst_data,
    is_unsupported_analyst_market,
)
from tests.conftest import create_test_app


class TestIsUnsupportedAnalystMarket:
    """Helper 직접 검증 — analyst 외 endpoint 가 추후 같은 helper 쓸 수 있도록."""

    def test_kospi_kosdaq_suffixes(self):
        assert is_unsupported_analyst_market("005930.KS") is True
        assert is_unsupported_analyst_market("263750.KQ") is True

    def test_case_insensitive_via_normalize(self):
        # helper 자체가 strip + upper 처리 — handler 와 동일한 normalize 보장
        assert is_unsupported_analyst_market("005930.ks") is True
        assert is_unsupported_analyst_market("  263750.kq  ") is True

    def test_us_and_unknown_are_supported(self):
        assert is_unsupported_analyst_market("GOOGL") is False
        assert is_unsupported_analyst_market("AAPL") is False
        assert is_unsupported_analyst_market("0700.HK") is False  # 향후 추가 시 본 케이스 갱신


class TestAnalystDataKRUnsupported:
    @pytest.mark.asyncio
    async def test_kospi_ticker_returns_unsupported(self):
        result = await get_analyst_data(symbol="005930.KS", user_id="test-user")
        assert result.symbol == "005930.KS"
        assert result.unsupported is True
        assert result.message is not None
        assert result.priceTargets is None
        assert result.grades == []

    @pytest.mark.asyncio
    async def test_kosdaq_ticker_returns_unsupported(self):
        result = await get_analyst_data(symbol="263750.KQ", user_id="test-user")
        assert result.unsupported is True


# ---------------------------------------------------------------------------
# Router-level 통합 — /overview KR 분기 (FastAPI dependency / 직렬화 검증)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def overview_client():
    from src.server.app.market_data import router

    app = create_test_app(router)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_overview_router_kr_success(overview_client):
    """KoreanFundamentalsSource 가 정상 응답 시 quote + performance 포함된 200 응답."""
    artifact = {
        "symbol": "005930.KS",
        "name": None,
        "quote": {
            "price": 75000.0, "change": 500.0, "changePct": 0.67,
            "marketCap": 500_000_000_000_000.0, "yearHigh": 88000.0,
        },
        "performance": {"1D": 0.67, "5D": 1.5, "1M": 3.2, "3M": -2.1, "6M": 5.0, "1Y": 25.0, "YTD": 10.0},
        "analystRatings": None,
        "quarterlyFundamentals": None,
        "earningsSurprises": None,
        "cashFlow": None,
        "revenueByProduct": None,
        "revenueByGeo": None,
    }
    cache_mock = AsyncMock()
    cache_mock.get = AsyncMock(return_value=None)  # cache miss
    cache_mock.set = AsyncMock(return_value=True)

    with patch(
        "src.data_client.korean.fundamentals_source.KoreanFundamentalsSource.get_overview",
        new=AsyncMock(return_value=artifact),
    ), patch(
        "src.utils.cache.redis_cache.get_cache_client", return_value=cache_mock,
    ):
        resp = await overview_client.get("/api/v1/market-data/stocks/005930.KS/overview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "005930.KS"
    assert body["unsupported"] is False
    assert body["quote"]["marketCap"] == 500_000_000_000_000.0
    assert body["performance"]["1Y"] == 25.0


@pytest.mark.asyncio
async def test_overview_router_kr_partial_falls_back_to_unsupported(overview_client):
    """KoreanFundamentalsSource 가 _partial=True 반환 시 unsupported 응답 + negative cache 기록."""
    cache_mock = AsyncMock()
    cache_mock.get = AsyncMock(return_value=None)
    cache_mock.set = AsyncMock(return_value=True)

    with patch(
        "src.data_client.korean.fundamentals_source.KoreanFundamentalsSource.get_overview",
        new=AsyncMock(return_value={"symbol": "005930.KS", "_partial": True, "quote": None, "performance": None}),
    ), patch(
        "src.utils.cache.redis_cache.get_cache_client", return_value=cache_mock,
    ):
        resp = await overview_client.get("/api/v1/market-data/stocks/005930.KS/overview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["unsupported"] is True
    assert body["message"] is not None
    assert "unavailable" in body["message"].lower()
    # negative cache 기록 검증 — TTL 60s 로 burst 보호
    cache_mock.set.assert_awaited_once()
    _, kwargs = cache_mock.set.call_args
    assert kwargs.get("ttl") == 60


@pytest.mark.asyncio
async def test_overview_router_kr_exception_falls_back_with_negative_cache(overview_client):
    """KoreanFundamentalsSource 가 예외 raise 시 unsupported 응답 + negative cache 기록.

    회귀 방지 — 이전엔 exception 분기에서 cache.set 누락돼 outage 시 매 요청마다 외부
    source 재호출. _partial 와 동일하게 60s TTL 로 burst 보호.
    """
    cache_mock = AsyncMock()
    cache_mock.get = AsyncMock(return_value=None)
    cache_mock.set = AsyncMock(return_value=True)

    with patch(
        "src.data_client.korean.fundamentals_source.KoreanFundamentalsSource.get_overview",
        new=AsyncMock(side_effect=Exception("yf upstream timeout")),
    ), patch(
        "src.utils.cache.redis_cache.get_cache_client", return_value=cache_mock,
    ):
        resp = await overview_client.get("/api/v1/market-data/stocks/005930.KS/overview")

    assert resp.status_code == 200
    body = resp.json()
    assert body["unsupported"] is True
    assert body["message"] is not None
    assert "unavailable" in body["message"].lower()
    cache_mock.set.assert_awaited_once()
    _, kwargs = cache_mock.set.call_args
    assert kwargs.get("ttl") == 60

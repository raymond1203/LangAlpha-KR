"""
Tests for /overview, /analyst-data graceful 미지원 응답 (issue #33 backend slice).

KR ticker (.KS / .KQ) 호출 시 200 with unsupported=True 로 응답해야 함 — frontend
가 graceful "한국 시장 미지원" 카드 렌더할 수 있도록. user_id / DB / cache 모두
무관 (early-return 분기) 이라 mock 없이 직접 함수 호출.
"""

import pytest

from src.server.app.market_data import (
    get_analyst_data,
    get_company_overview,
    is_unsupported_market,
)


class TestIsUnsupportedMarket:
    """Helper 직접 검증 — overview/analyst 외 endpoint 가 추후 같은 helper 쓸 수 있도록."""

    def test_kospi_kosdaq_suffixes(self):
        assert is_unsupported_market("005930.KS") is True
        assert is_unsupported_market("263750.KQ") is True

    def test_case_insensitive_via_normalize(self):
        # helper 자체가 strip + upper 처리 — handler 와 동일한 normalize 보장
        assert is_unsupported_market("005930.ks") is True
        assert is_unsupported_market("  263750.kq  ") is True

    def test_us_and_unknown_are_supported(self):
        assert is_unsupported_market("GOOGL") is False
        assert is_unsupported_market("AAPL") is False
        assert is_unsupported_market("0700.HK") is False  # 향후 추가 시 본 케이스 갱신


class TestCompanyOverviewKRUnsupported:
    @pytest.mark.asyncio
    async def test_kospi_ticker_returns_unsupported(self):
        result = await get_company_overview(symbol="005930.KS", user_id="test-user")
        assert result.symbol == "005930.KS"
        assert result.unsupported is True
        # message 는 영어 fallback — frontend 가 i18n key 로 사용자 locale 에 맞게 표시.
        assert result.message is not None
        assert "supported" in result.message.lower()
        # 모든 데이터 필드는 None — frontend 가 안전하게 빈 카드 렌더
        assert result.quote is None
        assert result.performance is None
        assert result.quarterlyFundamentals is None

    @pytest.mark.asyncio
    async def test_kosdaq_ticker_returns_unsupported(self):
        result = await get_company_overview(symbol="263750.KQ", user_id="test-user")
        assert result.unsupported is True
        assert result.symbol == "263750.KQ"

    @pytest.mark.asyncio
    async def test_lowercase_kr_suffix_returns_unsupported(self):
        # symbol 은 backend 에서 strip().upper() 처리되므로 소문자/공백도 매칭.
        # 반환되는 symbol 도 normalize 된 형태인지 검증 — 회귀 방지.
        result = await get_company_overview(symbol="  005930.ks  ", user_id="test-user")
        assert result.unsupported is True
        assert result.symbol == "005930.KS"


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

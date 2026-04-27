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
)


class TestCompanyOverviewKRUnsupported:
    @pytest.mark.asyncio
    async def test_kospi_ticker_returns_unsupported(self):
        result = await get_company_overview(symbol="005930.KS", user_id="test-user")
        assert result.symbol == "005930.KS"
        assert result.unsupported is True
        assert result.message is not None
        assert "한국" in result.message
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
        # symbol 은 backend 에서 upper() 처리되므로 소문자도 매칭돼야 함
        result = await get_company_overview(symbol="005930.ks", user_id="test-user")
        assert result.unsupported is True


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

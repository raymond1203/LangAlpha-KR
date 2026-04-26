"""Tests for the MarketDataProvider chain-of-responsibility pattern."""

from __future__ import annotations

import pytest

from src.data_client.market_data_provider import (
    MarketDataProvider,
    ProviderEntry,
    symbol_market,
)


# ---------------------------------------------------------------------------
# Helpers — lightweight fake data sources
# ---------------------------------------------------------------------------

class FakeSource:
    """Configurable fake MarketDataSource for testing."""

    def __init__(self, name: str = "fake", *, fail: bool = False):
        self.name = name
        self.fail = fail
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def get_intraday(self, **kwargs):
        self.calls.append(("get_intraday", kwargs))
        if self.fail:
            raise RuntimeError(f"{self.name} intraday error")
        return [{"date": "2025-01-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}]

    async def get_daily(self, **kwargs):
        self.calls.append(("get_daily", kwargs))
        if self.fail:
            raise RuntimeError(f"{self.name} daily error")
        return [{"date": "2025-01-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}]

    async def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# symbol_market tests
# ---------------------------------------------------------------------------

class TestSymbolMarket:
    def test_bare_symbol_is_us(self):
        assert symbol_market("AAPL") == "us"

    def test_us_suffix(self):
        assert symbol_market("AAPL.US") == "us"

    def test_hk_suffix(self):
        assert symbol_market("0700.HK") == "hk"

    def test_shanghai_suffix(self):
        assert symbol_market("600519.SS") == "cn"

    def test_shenzhen_suffix(self):
        assert symbol_market("000001.SZ") == "cn"

    def test_london_suffix(self):
        assert symbol_market("SHEL.L") == "uk"

    def test_tokyo_suffix(self):
        assert symbol_market("7203.T") == "jp"

    def test_unknown_suffix(self):
        assert symbol_market("XYZ.ZZ") == "other"

    def test_case_insensitive(self):
        assert symbol_market("0700.hk") == "hk"


# ---------------------------------------------------------------------------
# MarketDataProvider tests
# ---------------------------------------------------------------------------

class TestMarketDataProvider:
    @pytest.mark.asyncio
    async def test_single_provider_passthrough(self):
        src = FakeSource("primary")
        provider = MarketDataProvider([ProviderEntry("primary", src, {"all"})])
        result = await provider.get_intraday(symbol="AAPL", interval="1min")
        assert len(result) == 1
        assert src.calls == [("get_intraday", {"symbol": "AAPL", "interval": "1min", "from_date": None, "to_date": None, "is_index": False, "user_id": None})]

    @pytest.mark.asyncio
    async def test_us_symbol_primary_succeeds_no_fallback(self):
        primary = FakeSource("ginlix")
        fallback = FakeSource("fmp")
        provider = MarketDataProvider([
            ProviderEntry("ginlix", primary, {"us"}),
            ProviderEntry("fmp", fallback, {"all"}),
        ])
        result = await provider.get_intraday(symbol="AAPL", interval="1min")
        assert len(result) == 1
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 0

    @pytest.mark.asyncio
    async def test_us_symbol_primary_fails_fallback_called(self):
        primary = FakeSource("ginlix", fail=True)
        fallback = FakeSource("fmp")
        provider = MarketDataProvider([
            ProviderEntry("ginlix", primary, {"us"}),
            ProviderEntry("fmp", fallback, {"all"}),
        ])
        result = await provider.get_intraday(symbol="AAPL", interval="1min")
        assert len(result) == 1
        assert len(primary.calls) == 1
        assert len(fallback.calls) == 1

    @pytest.mark.asyncio
    async def test_non_us_symbol_skips_us_only_provider(self):
        us_only = FakeSource("ginlix")
        global_src = FakeSource("fmp")
        provider = MarketDataProvider([
            ProviderEntry("ginlix", us_only, {"us"}),
            ProviderEntry("fmp", global_src, {"all"}),
        ])
        result = await provider.get_daily(symbol="0700.HK")
        assert len(result) == 1
        assert len(us_only.calls) == 0  # skipped — no HK market coverage
        assert len(global_src.calls) == 1

    @pytest.mark.asyncio
    async def test_all_providers_fail_raises_last_exception(self):
        src1 = FakeSource("a", fail=True)
        src2 = FakeSource("b", fail=True)
        provider = MarketDataProvider([
            ProviderEntry("a", src1, {"all"}),
            ProviderEntry("b", src2, {"all"}),
        ])
        with pytest.raises(RuntimeError, match="b daily error"):
            await provider.get_daily(symbol="AAPL")

    @pytest.mark.asyncio
    async def test_no_providers_for_market_raises(self):
        us_only = FakeSource("ginlix")
        provider = MarketDataProvider([
            ProviderEntry("ginlix", us_only, {"us"}),
        ])
        with pytest.raises(RuntimeError, match="No data source configured"):
            await provider.get_intraday(symbol="0700.HK", interval="1min")

    @pytest.mark.asyncio
    async def test_close_closes_all_sources(self):
        src1 = FakeSource("a")
        src2 = FakeSource("b")
        provider = MarketDataProvider([
            ProviderEntry("a", src1, {"all"}),
            ProviderEntry("b", src2, {"all"}),
        ])
        await provider.close()
        assert src1.closed
        assert src2.closed

    @pytest.mark.asyncio
    async def test_close_continues_on_error(self):
        """Even if one source's close() raises, other sources are still closed."""
        class FailCloseSource(FakeSource):
            async def close(self):
                raise RuntimeError("close failed")

        src1 = FailCloseSource("a")
        src2 = FakeSource("b")
        provider = MarketDataProvider([
            ProviderEntry("a", src1, {"all"}),
            ProviderEntry("b", src2, {"all"}),
        ])
        await provider.close()  # should not raise
        assert src2.closed

    def test_source_names(self):
        provider = MarketDataProvider([
            ProviderEntry("ginlix-data", FakeSource(), {"us"}),
            ProviderEntry("fmp", FakeSource(), {"all"}),
        ])
        assert provider.source_names == ["ginlix-data", "fmp"]

    @pytest.mark.asyncio
    async def test_get_daily_passthrough(self):
        src = FakeSource("fmp")
        provider = MarketDataProvider([ProviderEntry("fmp", src, {"all"})])
        result = await provider.get_daily(symbol="MSFT", from_date="2025-01-01", to_date="2025-06-01")
        assert len(result) == 1
        assert src.calls[0] == ("get_daily", {
            "symbol": "MSFT",
            "from_date": "2025-01-01",
            "to_date": "2025-06-01",
            "is_index": False,
            "user_id": None,
        })

    @pytest.mark.asyncio
    async def test_multi_market_provider_routing(self):
        """A provider covering {hk, cn} should be used for HK and CN symbols."""
        asia_src = FakeSource("asia")
        global_src = FakeSource("fmp")
        provider = MarketDataProvider([
            ProviderEntry("asia", asia_src, {"hk", "cn"}),
            ProviderEntry("fmp", global_src, {"all"}),
        ])

        await provider.get_intraday(symbol="0700.HK", interval="1min")
        assert len(asia_src.calls) == 1
        assert len(global_src.calls) == 0

        await provider.get_intraday(symbol="600519.SS", interval="1min")
        assert len(asia_src.calls) == 2
        assert len(global_src.calls) == 0

        # US symbol should skip asia provider
        await provider.get_intraday(symbol="AAPL", interval="1min")
        assert len(asia_src.calls) == 2  # unchanged
        assert len(global_src.calls) == 1


# ---------------------------------------------------------------------------
# get_market_status region routing (issue #37)
# ---------------------------------------------------------------------------


class _RegionAwareSource:
    """get_market_status 만 가진 fake. region 인자를 받아 자기가 안 다루는 region
    이면 NotImplementedError 를 raise — 실제 KoreanDataSource / YfinanceDataSource
    가 이슈 #37 fix 후 동작하는 방식과 동일.
    """

    def __init__(self, name: str, supports: set[str]):
        self.name = name
        self.supports = supports
        self.calls: list[dict] = []

    async def get_market_status(self, user_id=None, region=None):
        self.calls.append({"user_id": user_id, "region": region})
        if region is not None and region not in self.supports:
            raise NotImplementedError(
                f"{self.name} only supports {self.supports}, got {region!r}"
            )
        return {"market": "open" if "open" in self.supports else "closed", "_source": self.name}

    async def close(self):
        pass


class TestMarketStatusRegionRouting:
    @pytest.mark.asyncio
    async def test_kr_region_routes_to_korean_source(self):
        """region='kr' → KR source 응답. US source 는 NotImplementedError 로 skip."""
        kr_src = _RegionAwareSource("korean", supports={"kr", "open"})
        us_src = _RegionAwareSource("yfinance", supports={"us"})
        provider = MarketDataProvider([
            ProviderEntry("yfinance", us_src, {"all"}),  # 'all' 마킹이지만 region='kr' 거절
            ProviderEntry("korean", kr_src, {"kr"}),
        ])
        result = await provider.get_market_status(region="kr")
        assert result["_source"] == "korean"
        # us_src 가 먼저 시도됐으나 NotImplementedError 로 skip 됐는지 확인
        assert us_src.calls == [{"user_id": None, "region": "kr"}]
        assert kr_src.calls == [{"user_id": None, "region": "kr"}]

    @pytest.mark.asyncio
    async def test_us_region_routes_to_us_source(self):
        kr_src = _RegionAwareSource("korean", supports={"kr"})
        us_src = _RegionAwareSource("yfinance", supports={"us", "open"})
        provider = MarketDataProvider([
            ProviderEntry("korean", kr_src, {"kr"}),
            ProviderEntry("yfinance", us_src, {"all"}),
        ])
        result = await provider.get_market_status(region="us")
        assert result["_source"] == "yfinance"
        # KR 은 region='us' 매칭에서 제외 (markets={'kr'} 만, 'all' 없음)
        assert kr_src.calls == []
        assert us_src.calls == [{"user_id": None, "region": "us"}]

    @pytest.mark.asyncio
    async def test_region_none_backward_compat(self):
        """region=None 은 기존 동작 — chain 의 첫 번째 source 가 응답."""
        kr_src = _RegionAwareSource("korean", supports={"kr"})
        us_src = _RegionAwareSource("yfinance", supports={"us", "open"})
        provider = MarketDataProvider([
            ProviderEntry("korean", kr_src, {"kr"}),
            ProviderEntry("yfinance", us_src, {"all"}),
        ])
        # region=None 은 모든 candidates. korean 이 첫 호출 — region=None 이라 거절 안 함.
        result = await provider.get_market_status()
        # kr source 가 region=None 을 받아 KR status 반환
        assert result["_source"] == "korean"

    @pytest.mark.asyncio
    async def test_unsupported_region_raises(self):
        """어떤 source 도 region 을 지원 안 하면 마지막 NotImplementedError 가 raise."""
        us_src = _RegionAwareSource("yfinance", supports={"us"})
        provider = MarketDataProvider([
            ProviderEntry("yfinance", us_src, {"all"}),
        ])
        with pytest.raises(NotImplementedError):
            await provider.get_market_status(region="kr")

    @pytest.mark.asyncio
    async def test_korean_source_unit_rejects_non_kr_region(self):
        """KoreanDataSource 자체가 region='us' 호출에 NotImplementedError."""
        from src.data_client.korean.data_source import KoreanDataSource

        source = KoreanDataSource()
        with pytest.raises(NotImplementedError, match="region='kr'"):
            await source.get_market_status(region="us")
        # region='kr' 또는 None 은 정상
        result = await source.get_market_status(region="kr")
        assert "market" in result
        result2 = await source.get_market_status(region=None)
        assert "market" in result2


# ---------------------------------------------------------------------------
# FMPDataSource interval guard tests
# ---------------------------------------------------------------------------

class TestFMPDataSourceIntervalGuard:
    @pytest.mark.asyncio
    async def test_fmp_rejects_1s_interval(self):
        from src.data_client.fmp.data_source import FMPDataSource
        source = FMPDataSource()
        with pytest.raises(ValueError, match="not supported"):
            await source.get_intraday(symbol="AAPL", interval="1s")

    @pytest.mark.asyncio
    async def test_chain_surfaces_unsupported_interval_error(self):
        """When the only provider rejects an interval, the error propagates."""
        class IntervalAwareSource:
            async def get_intraday(self, **kwargs):
                if kwargs.get("interval") == "1s":
                    raise ValueError("1s not supported")
                return [{"date": "2025-01-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}]
            async def get_daily(self, **kwargs):
                return []
            async def close(self):
                pass

        provider = MarketDataProvider([ProviderEntry("only", IntervalAwareSource(), {"all"})])
        with pytest.raises(ValueError, match="1s not supported"):
            await provider.get_intraday(symbol="AAPL", interval="1s")


# ---------------------------------------------------------------------------
# Config accessor tests
# ---------------------------------------------------------------------------

class TestConfigAccessor:
    def test_default_providers_when_no_config(self):
        """get_market_data_providers returns FMP-only when key is missing."""
        from src.config.settings import get_nested_config
        # The function uses get_nested_config with a default
        result = get_nested_config("market_data.providers_nonexistent", [{"name": "fmp", "markets": ["all"]}])
        assert result == [{"name": "fmp", "markets": ["all"]}]

    def test_actual_config_has_providers(self):
        """config.yaml should have market_data.providers configured."""
        from src.config.settings import get_market_data_providers
        providers = get_market_data_providers()
        assert isinstance(providers, list)
        assert len(providers) >= 1
        names = [p["name"] for p in providers]
        assert "fmp" in names

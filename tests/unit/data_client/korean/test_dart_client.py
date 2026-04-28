"""Tests for src.data_client.korean.dart_client (#52).

Covers three concerns the bare OpenDartReader doesn't:
- Singleton lifecycle + concurrent init guard
- Redis cache hit/miss with year-based TTL
- Retry + exponential backoff (sleep mocked for speed)

Tests for ``_extract_dart_amount`` / ``_safe_float`` / ``_row_cumulative_amount``
also live here — the logic moved out of fundamentals_source as part of #52.
"""

from __future__ import annotations

import threading
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pandas as pd
import pytest

from src.data_client.korean import dart_client as dc


@pytest.fixture(autouse=True)
def _reset_dart_singleton(monkeypatch):
    """Each test starts with a fresh client init slate."""
    dc.reset_singleton_for_test()
    yield
    dc.reset_singleton_for_test()


@pytest.fixture
def fast_retry(monkeypatch):
    """Skip real backoff sleeps so retry tests run in ms, not seconds."""
    monkeypatch.setattr(dc.time, "sleep", lambda _s: None)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

class TestGetDartClient:
    def test_returns_none_without_api_key(self, monkeypatch):
        monkeypatch.delenv("DART_API_KEY", raising=False)
        assert dc._get_dart_client() is None
        # Second call short-circuits via init_attempted flag — still None.
        assert dc._get_dart_client() is None

    def test_lock_prevents_concurrent_init_returning_none(self, monkeypatch):
        """Two threads racing init must both receive the constructed client.

        Regression: an earlier version flipped ``init_attempted`` *before*
        ``OpenDartReader(...)`` returned, so a second thread mid-construction
        saw ``None``.
        """
        monkeypatch.setenv("DART_API_KEY", "fake-key")
        init_call_count = 0
        init_started = threading.Event()
        init_can_finish = threading.Event()

        class SlowOpenDartReader:
            def __init__(self, _key):
                nonlocal init_call_count
                init_call_count += 1
                init_started.set()
                init_can_finish.wait(timeout=2)

        import sys
        sys.modules["OpenDartReader"] = SlowOpenDartReader  # type: ignore[assignment]

        results: list[Any] = []

        def call_get_client():
            results.append(dc._get_dart_client())

        try:
            t1 = threading.Thread(target=call_get_client)
            t2 = threading.Thread(target=call_get_client)
            t1.start()
            init_started.wait(timeout=2)
            t2.start()
            init_can_finish.set()
            t1.join(timeout=3)
            t2.join(timeout=3)
        finally:
            del sys.modules["OpenDartReader"]

        assert init_call_count == 1
        assert all(r is not None for r in results), f"got None: {results}"
        assert results[0] is results[1]


# ---------------------------------------------------------------------------
# _safe_float / _row_cumulative_amount / _extract_dart_amount
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def test_valid_numeric(self):
        assert dc._safe_float(1.5) == 1.5
        assert dc._safe_float(0) == 0.0
        assert dc._safe_float("3.14") == 3.14

    def test_none_returns_none(self):
        assert dc._safe_float(None) is None

    def test_nan_returns_none(self):
        assert dc._safe_float(float("nan")) is None

    def test_non_numeric_returns_none(self):
        assert dc._safe_float("abc") is None
        assert dc._safe_float({}) is None

    def test_strips_thousands_comma(self):
        assert dc._safe_float("79,140,503,000,000") == 79_140_503_000_000.0


def _make_dart_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


class TestExtractDartAmount:
    def test_account_id_match_takes_priority(self):
        df = _make_dart_df([
            {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": "200"},
            {"sj_div": "IS", "account_id": "dart_OtherSales", "account_nm": "기타매출", "thstrm_amount": "999"},
        ])
        assert dc._extract_dart_amount(df, dc._REVENUE_IDS, dc._REVENUE_KEYWORDS) == 200.0

    def test_falls_back_to_account_nm_when_no_id_match(self):
        df = _make_dart_df([
            {"sj_div": "IS", "account_id": "custom_kgaap_revenue", "account_nm": "매출액", "thstrm_amount": "150"},
        ])
        assert dc._extract_dart_amount(df, dc._REVENUE_IDS, dc._REVENUE_KEYWORDS) == 150.0

    def test_filters_by_sj_div(self):
        df = _make_dart_df([
            {"sj_div": "CF", "account_id": "x", "account_nm": "영업이익", "thstrm_amount": "999"},
        ])
        assert dc._extract_dart_amount(df, (), dc._OPERATING_INCOME_KEYWORDS, sj_divs=("IS",)) is None
        assert dc._extract_dart_amount(df, (), dc._OPERATING_INCOME_KEYWORDS, sj_divs=("CF",)) == 999.0

    def test_handles_missing_columns_safely(self):
        df = pd.DataFrame({"sj_div": ["IS"], "thstrm_amount": ["100"]})
        assert dc._extract_dart_amount(df, dc._REVENUE_IDS, dc._REVENUE_KEYWORDS) is None

    def test_handles_empty_dataframe(self):
        assert dc._extract_dart_amount(pd.DataFrame(), dc._REVENUE_IDS, dc._REVENUE_KEYWORDS) is None
        assert dc._extract_dart_amount(None, dc._REVENUE_IDS, dc._REVENUE_KEYWORDS) is None  # type: ignore[arg-type]

    def test_handles_dict_response_from_dart_no_data(self):
        no_data = {"status": "013", "message": "조회된 데이타가 없습니다."}
        assert dc._extract_dart_amount(no_data, dc._REVENUE_IDS, dc._REVENUE_KEYWORDS) is None  # type: ignore[arg-type]

    def test_prefers_thstrm_add_amount_for_is_rows(self):
        """IS rows: thstrm_amount = 단일 분기, thstrm_add_amount = 누적. add 우선."""
        df = _make_dart_df([{
            "sj_div": "IS",
            "account_id": "ifrs-full_Revenue",
            "account_nm": "매출액",
            "thstrm_amount": "74,566,317,000,000",
            "thstrm_add_amount": "153,706,820,000,000",
        }])
        assert dc._extract_dart_amount(df, dc._REVENUE_IDS, dc._REVENUE_KEYWORDS) == 153_706_820_000_000.0

    def test_falls_back_to_thstrm_amount_when_add_empty(self):
        """FY 보고서 / Q1 / CF rows: thstrm_add_amount 비어있음 → amount 가 누적."""
        df_fy = _make_dart_df([{
            "sj_div": "IS",
            "account_id": "ifrs-full_Revenue",
            "account_nm": "매출액",
            "thstrm_amount": "333,605,938,000,000",
            "thstrm_add_amount": "",
        }])
        assert dc._extract_dart_amount(df_fy, dc._REVENUE_IDS, dc._REVENUE_KEYWORDS) == 333_605_938_000_000.0

        df_cf = _make_dart_df([{
            "sj_div": "CF",
            "account_id": "ifrs-full_CashFlowsFromUsedInOperatingActivities",
            "account_nm": "영업활동현금흐름",
            "thstrm_amount": "33,941,002,000,000",
            "thstrm_add_amount": float("nan"),
        }])
        assert dc._extract_dart_amount(df_cf, dc._OPERATING_CF_IDS, dc._OPERATING_CF_KEYWORDS, sj_divs=("CF",)) == 33_941_002_000_000.0

    def test_treats_zero_thstrm_add_amount_as_valid(self):
        """add_amount = 0 도 valid 누적치 — amount 로 fallback 하면 차분 결과 오염."""
        df = _make_dart_df([{
            "sj_div": "CF",
            "account_id": "ifrs-full_CashFlowsFromUsedInInvestingActivities",
            "account_nm": "투자활동현금흐름",
            "thstrm_amount": "999",
            "thstrm_add_amount": "0",
        }])
        assert dc._extract_dart_amount(df, dc._INVESTING_CF_IDS, dc._INVESTING_CF_KEYWORDS, sj_divs=("CF",)) == 0.0


# ---------------------------------------------------------------------------
# fetch_finstate_extracted — cache + retry layer
# ---------------------------------------------------------------------------


def _build_finstate_df(
    revenue_cum: float, op_cum: float, net_cum: float,
    op_cf_cum: float, inv_cf_cum: float, fin_cf_cum: float,
) -> pd.DataFrame:
    return _make_dart_df([
        {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": str(revenue_cum)},
        {"sj_div": "IS", "account_id": "dart_OperatingIncomeLoss", "account_nm": "영업이익", "thstrm_amount": str(op_cum)},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLoss", "account_nm": "분기순이익", "thstrm_amount": str(net_cum)},
        {"sj_div": "CF", "account_id": "ifrs-full_CashFlowsFromUsedInOperatingActivities", "account_nm": "영업활동현금흐름", "thstrm_amount": str(op_cf_cum)},
        {"sj_div": "CF", "account_id": "ifrs-full_CashFlowsFromUsedInInvestingActivities", "account_nm": "투자활동현금흐름", "thstrm_amount": str(inv_cf_cum)},
        {"sj_div": "CF", "account_id": "ifrs-full_CashFlowsFromUsedInFinancingActivities", "account_nm": "재무활동현금흐름", "thstrm_amount": str(fin_cf_cum)},
    ])


@pytest.fixture
def fake_cache(monkeypatch):
    """In-memory cache stub backing the redis client."""
    storage: dict[str, Any] = {}
    client = MagicMock()
    client.get = AsyncMock(side_effect=lambda key: storage.get(key))

    async def _set(key, value, ttl=None):
        storage[key] = value
        client.last_ttl = ttl
        return True

    client.set = AsyncMock(side_effect=_set)
    client.last_ttl = None
    monkeypatch.setattr(dc, "get_cache_client", lambda: client)
    return client, storage


@pytest.mark.asyncio
async def test_fetch_finstate_extracted_returns_none_when_no_client(monkeypatch, fake_cache):
    monkeypatch.setattr(dc, "_get_dart_client", lambda: None)
    result = await dc.fetch_finstate_extracted("005930", 2025, "11013")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_finstate_extracted_caches_extracted_values(monkeypatch, fake_cache):
    """First call hits DART, second call serves from cache (no DART hit)."""
    cache_client, storage = fake_cache
    fake_dart = MagicMock()
    fake_dart.finstate_all = MagicMock(return_value=_build_finstate_df(100, 10, 8, 20, -15, -3))
    monkeypatch.setattr(dc, "_get_dart_client", lambda: fake_dart)

    # First call — MISS → DART hit → cache
    first = await dc.fetch_finstate_extracted("005930", 2024, "11013")
    assert first == {
        "revenue": 100.0,
        "operatingIncome": 10.0,
        "netIncome": 8.0,
        "operatingCashFlow": 20.0,
        "investingCashFlow": -15.0,
        "financingCashFlow": -3.0,
    }
    assert fake_dart.finstate_all.call_count == 1
    assert "dart:finstate:005930:2024:11013:CFS" in storage

    # Second call — HIT → no DART hit
    second = await dc.fetch_finstate_extracted("005930", 2024, "11013")
    assert second == first
    assert fake_dart.finstate_all.call_count == 1


@pytest.mark.asyncio
async def test_fetch_finstate_extracted_uses_long_ttl_for_past_year(monkeypatch, fake_cache):
    """Past-year filings are immutable — cache for ~1 year."""
    cache_client, _storage = fake_cache
    fake_dart = MagicMock()
    fake_dart.finstate_all = MagicMock(return_value=_build_finstate_df(100, 10, 8, 20, -15, -3))
    monkeypatch.setattr(dc, "_get_dart_client", lambda: fake_dart)

    # Pin "current year" to 2026 so 2024 is firmly in the past.
    import datetime as _dt
    class FixedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 6, 1, tzinfo=tz)
    monkeypatch.setattr(dc, "datetime", FixedDatetime)

    await dc.fetch_finstate_extracted("005930", 2024, "11013")
    # Past year + has_data → 1 year TTL
    assert cache_client.last_ttl == dc._TTL_PAST_YEAR_S


@pytest.mark.asyncio
async def test_fetch_finstate_extracted_uses_short_ttl_for_current_year(monkeypatch, fake_cache):
    """Current-year filings can land mid-year — cache for ~1 day."""
    cache_client, _storage = fake_cache
    fake_dart = MagicMock()
    fake_dart.finstate_all = MagicMock(return_value=_build_finstate_df(100, 10, 8, 20, -15, -3))
    monkeypatch.setattr(dc, "_get_dart_client", lambda: fake_dart)

    import datetime as _dt
    class FixedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 6, 1, tzinfo=tz)
    monkeypatch.setattr(dc, "datetime", FixedDatetime)

    await dc.fetch_finstate_extracted("005930", 2026, "11013")
    assert cache_client.last_ttl == dc._TTL_CURRENT_YEAR_S


@pytest.mark.asyncio
async def test_fetch_finstate_extracted_caches_negative_result(monkeypatch, fake_cache):
    """Genuine 'no data' answers are cached too (shorter TTL)."""
    cache_client, _storage = fake_cache
    fake_dart = MagicMock()
    fake_dart.finstate_all = MagicMock(return_value=pd.DataFrame())
    monkeypatch.setattr(dc, "_get_dart_client", lambda: fake_dart)

    result = await dc.fetch_finstate_extracted("005930", 2024, "11013")
    assert result == dc._EMPTY_EXTRACT
    # Negative + past year → 7-day TTL
    assert cache_client.last_ttl == dc._NEG_TTL_PAST_YEAR_S


@pytest.mark.asyncio
async def test_fetch_finstate_extracted_retries_on_exception(monkeypatch, fake_cache, fast_retry):
    """Two DART exceptions then success — caller never sees failure."""
    fake_dart = MagicMock()
    successful_df = _build_finstate_df(100, 10, 8, 20, -15, -3)
    fake_dart.finstate_all = MagicMock(side_effect=[
        RuntimeError("DART rate limit"),
        RuntimeError("network glitch"),
        successful_df,
    ])
    monkeypatch.setattr(dc, "_get_dart_client", lambda: fake_dart)

    result = await dc.fetch_finstate_extracted("005930", 2024, "11013")
    assert result is not None
    assert result["revenue"] == 100.0
    assert fake_dart.finstate_all.call_count == 3


@pytest.mark.asyncio
async def test_fetch_finstate_extracted_returns_empty_on_retry_exhaustion(monkeypatch, fake_cache, fast_retry):
    """All retries fail → return _EMPTY_EXTRACT, do NOT cache (next call retries)."""
    cache_client, storage = fake_cache
    fake_dart = MagicMock()
    fake_dart.finstate_all = MagicMock(side_effect=RuntimeError("DART down"))
    monkeypatch.setattr(dc, "_get_dart_client", lambda: fake_dart)

    result = await dc.fetch_finstate_extracted("005930", 2024, "11013")
    assert result == dc._EMPTY_EXTRACT
    # 1 initial + 3 retries
    assert fake_dart.finstate_all.call_count == 4
    # Failure path doesn't cache — so the next call retries from scratch
    assert "dart:finstate:005930:2024:11013:CFS" not in storage


@pytest.mark.asyncio
async def test_fetch_finstate_extracted_cache_hit_skips_retry_path(monkeypatch, fake_cache, fast_retry):
    """Cache hit short-circuits before the DART client is even consulted."""
    cache_client, storage = fake_cache
    storage["dart:finstate:005930:2024:11013:CFS"] = {
        "revenue": 42.0,
        "operatingIncome": None,
        "netIncome": None,
        "operatingCashFlow": None,
        "investingCashFlow": None,
        "financingCashFlow": None,
    }
    fake_dart = MagicMock()
    fake_dart.finstate_all = MagicMock(side_effect=AssertionError("should not be called"))
    monkeypatch.setattr(dc, "_get_dart_client", lambda: fake_dart)

    result = await dc.fetch_finstate_extracted("005930", 2024, "11013")
    assert result is not None
    assert result["revenue"] == 42.0
    fake_dart.finstate_all.assert_not_called()

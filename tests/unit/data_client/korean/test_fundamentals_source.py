"""Tests for KoreanFundamentalsSource (issue #42 + #52).

External calls (yfinance fast_info, pykrx OHLCV, DART) live in module-level
helpers so they can be ``monkeypatch.setattr`` 'd. DART access goes through
``dart_client.fetch_finstate_extracted`` (cache + retry layer); these tests
mock that single async function. dart_client's own internals (cache, retry,
extract) have dedicated tests in ``test_dart_client.py``.

Integration with real yf/pykrx/DART is out of scope for the unit suite.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data_client.korean import fundamentals_source as fs


def _make_extracted(
    revenue: float | None = None,
    op: float | None = None,
    net: float | None = None,
    op_cf: float | None = None,
    inv_cf: float | None = None,
    fin_cf: float | None = None,
) -> dict[str, float | None]:
    """Build a six-key extracted dict in the shape ``fetch_finstate_extracted`` returns."""
    return {
        "revenue": revenue,
        "operatingIncome": op,
        "netIncome": net,
        "operatingCashFlow": op_cf,
        "investingCashFlow": inv_cf,
        "financingCashFlow": fin_cf,
    }


def _patch_fixed_kst(monkeypatch, year: int, month: int = 12, day: int = 31) -> None:
    """Pin ``fs.datetime`` so quarterly/annual lookback windows are deterministic."""
    import datetime as _dt
    class FixedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(year, month, day, tzinfo=tz)
    monkeypatch.setattr(fs, "datetime", FixedDatetime)


# ---------------------------------------------------------------------------
# supports() — 정적 가드
# ---------------------------------------------------------------------------

class TestSupports:
    def test_kospi_kosdaq(self):
        assert fs.KoreanFundamentalsSource.supports("005930.KS") is True
        assert fs.KoreanFundamentalsSource.supports("263750.KQ") is True

    def test_case_insensitive(self):
        assert fs.KoreanFundamentalsSource.supports("005930.ks") is True

    def test_us_or_other_false(self):
        assert fs.KoreanFundamentalsSource.supports("GOOGL") is False
        assert fs.KoreanFundamentalsSource.supports("0700.HK") is False


# ---------------------------------------------------------------------------
# get_overview() — quote + performance + DART 통합
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_overview_returns_quote_and_performance(monkeypatch):
    fake_quote = {
        "price": 75000.0, "change": 500.0, "changePct": 0.67,
        "open": 74500.0, "previousClose": 74500.0,
        "dayLow": 74400.0, "dayHigh": 75200.0,
        "yearLow": 60000.0, "yearHigh": 88000.0,
        "volume": 12_000_000.0, "marketCap": 500_000_000_000_000.0,
        "shares": 6_700_000_000.0, "pe": None, "eps": None,
    }
    fake_perf = {"1D": 0.67, "5D": 1.5, "1M": 3.2, "3M": -2.1, "6M": 5.0, "1Y": 25.0, "YTD": 10.0}

    monkeypatch.setattr(fs, "_fetch_quote_from_yf", lambda symbol: fake_quote)
    monkeypatch.setattr(fs, "_fetch_performance_from_pykrx", lambda ticker: fake_perf)

    async def empty_quarterlies(_corp): return ([], [])
    async def empty_annuals(_corp): return ([], [])
    monkeypatch.setattr(fs, "_fetch_dart_quarterlies", empty_quarterlies)
    monkeypatch.setattr(fs, "_fetch_dart_annuals", empty_annuals)

    source = fs.KoreanFundamentalsSource()
    result = await source.get_overview("005930.KS")

    assert result["symbol"] == "005930.KS"
    assert result["quote"] == fake_quote
    assert result["performance"] == fake_perf
    assert result["analystRatings"] is None
    assert result["quarterlyFundamentals"] is None
    assert result["cashFlow"] is None
    assert result["annualFundamentals"] is None
    assert result["annualCashFlow"] is None


@pytest.mark.asyncio
async def test_get_overview_strips_whitespace_from_symbol(monkeypatch):
    captured: dict[str, str] = {}

    def capture_quote(sym):
        captured["yf_symbol"] = sym
        return {"price": 1.0}

    def capture_perf(ticker):
        captured["pykrx_ticker"] = ticker
        return {"1D": 0.0}

    async def capture_quarterlies(corp):
        captured["dart_quarterly_corp"] = corp
        return ([], [])

    async def capture_annuals(corp):
        captured["dart_annual_corp"] = corp
        return ([], [])

    monkeypatch.setattr(fs, "_fetch_quote_from_yf", capture_quote)
    monkeypatch.setattr(fs, "_fetch_performance_from_pykrx", capture_perf)
    monkeypatch.setattr(fs, "_fetch_dart_quarterlies", capture_quarterlies)
    monkeypatch.setattr(fs, "_fetch_dart_annuals", capture_annuals)

    source = fs.KoreanFundamentalsSource()
    result = await source.get_overview("  005930.KS  ")

    assert captured["yf_symbol"] == "005930.KS"
    assert captured["pykrx_ticker"] == "005930"
    assert captured["dart_quarterly_corp"] == "005930"
    assert captured["dart_annual_corp"] == "005930"
    assert result["symbol"] == "005930.KS"


@pytest.mark.asyncio
async def test_get_overview_yf_fail_returns_partial(monkeypatch):
    monkeypatch.setattr(fs, "_fetch_quote_from_yf", lambda symbol: {})
    monkeypatch.setattr(fs, "_fetch_performance_from_pykrx", lambda ticker: {"1D": 1.0})
    async def empty(_corp): return ([], [])
    monkeypatch.setattr(fs, "_fetch_dart_quarterlies", empty)
    monkeypatch.setattr(fs, "_fetch_dart_annuals", empty)

    source = fs.KoreanFundamentalsSource()
    result = await source.get_overview("005930.KS")

    assert result["_partial"] is True
    assert result["quote"] is None
    assert result["performance"] is None


@pytest.mark.asyncio
async def test_get_overview_includes_dart_quarterly_and_annual_data(monkeypatch):
    fundamentals = [
        {"period": "2025 Q3", "revenue": 80e12, "operatingIncome": 9e12, "netIncome": 7e12},
        {"period": "2025 Q4", "revenue": 85e12, "operatingIncome": 10.5e12, "netIncome": 8e12},
    ]
    cashflow = [
        {"period": "2025 Q3", "operatingCashFlow": 12e12, "investingCashFlow": -8e12, "financingCashFlow": -3e12},
        {"period": "2025 Q4", "operatingCashFlow": 14e12, "investingCashFlow": -9.5e12, "financingCashFlow": -3.5e12},
    ]
    annual_funds = [
        {"period": "2023", "revenue": 250e12, "operatingIncome": 6e12, "netIncome": 15e12},
        {"period": "2024", "revenue": 300e12, "operatingIncome": 32e12, "netIncome": 25e12},
    ]
    annual_cf = [
        {"period": "2023", "operatingCashFlow": 50e12, "investingCashFlow": -30e12, "financingCashFlow": -10e12},
        {"period": "2024", "operatingCashFlow": 60e12, "investingCashFlow": -35e12, "financingCashFlow": -12e12},
    ]
    monkeypatch.setattr(fs, "_fetch_quote_from_yf", lambda symbol: {"price": 75000.0})
    monkeypatch.setattr(fs, "_fetch_performance_from_pykrx", lambda ticker: {"1D": 0.5})
    async def quarterlies(_corp): return (fundamentals, cashflow)
    async def annuals(_corp): return (annual_funds, annual_cf)
    monkeypatch.setattr(fs, "_fetch_dart_quarterlies", quarterlies)
    monkeypatch.setattr(fs, "_fetch_dart_annuals", annuals)

    source = fs.KoreanFundamentalsSource()
    result = await source.get_overview("005930.KS")

    assert result["quarterlyFundamentals"] == fundamentals
    assert result["cashFlow"] == cashflow
    assert result["annualFundamentals"] == annual_funds
    assert result["annualCashFlow"] == annual_cf


# ---------------------------------------------------------------------------
# _fetch_performance_from_pykrx
# ---------------------------------------------------------------------------

def _make_ohlcv_df(closes: list[float]) -> pd.DataFrame:
    idx = pd.date_range(end="2026-04-27", periods=len(closes), freq="B", name="날짜")
    return pd.DataFrame({"종가": closes}, index=idx)


def test_performance_computes_percentage_against_lookback(monkeypatch):
    closes = [80.0] * 260 + [95.0, 96.0, 97.0, 98.0, 99.0, 100.0]
    monkeypatch.setattr(fs.stock, "get_market_ohlcv", lambda *a, **k: _make_ohlcv_df(closes))

    perf = fs._fetch_performance_from_pykrx("005930")
    assert perf["1D"] == pytest.approx((100 - 99) / 99 * 100, abs=0.01)
    assert perf["5D"] == pytest.approx((100 - 95) / 95 * 100, abs=0.01)
    assert perf["1Y"] is not None


def test_performance_returns_none_when_insufficient_data(monkeypatch):
    monkeypatch.setattr(
        fs.stock, "get_market_ohlcv", lambda *a, **k: _make_ohlcv_df([100.0, 101.0])
    )
    perf = fs._fetch_performance_from_pykrx("005930")
    assert perf["1D"] == pytest.approx((101 - 100) / 100 * 100, abs=0.01)
    assert perf["1M"] is None
    assert perf["1Y"] is None


def test_performance_returns_all_none_on_pykrx_failure(monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("KRX site down")
    monkeypatch.setattr(fs.stock, "get_market_ohlcv", boom)
    perf = fs._fetch_performance_from_pykrx("005930")
    assert all(v is None for v in perf.values())


def test_performance_returns_all_none_on_empty_df(monkeypatch):
    monkeypatch.setattr(fs.stock, "get_market_ohlcv", lambda *a, **k: pd.DataFrame())
    perf = fs._fetch_performance_from_pykrx("005930")
    assert all(v is None for v in perf.values())


def test_performance_returns_all_none_when_close_column_missing(monkeypatch):
    """Regression: direct df["종가"] indexing would propagate KeyError into 500."""
    df_no_close = pd.DataFrame({"시가": [100.0, 101.0], "거래량": [10, 20]})
    monkeypatch.setattr(fs.stock, "get_market_ohlcv", lambda *a, **k: df_no_close)
    perf = fs._fetch_performance_from_pykrx("005930")
    assert all(v is None for v in perf.values())


# ---------------------------------------------------------------------------
# _fetch_dart_quarterlies — 누적 → 분기 차분
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_dart_quarterlies_returns_empty_when_no_client(monkeypatch):
    """``fetch_finstate_extracted`` returns None (no API key) → degrade gracefully."""
    async def no_dart(*_args, **_kwargs):
        return None
    monkeypatch.setattr(fs, "fetch_finstate_extracted", no_dart)

    fundamentals, cashflow = await fs._fetch_dart_quarterlies("005930")
    assert fundamentals == []
    assert cashflow == []


@pytest.mark.asyncio
async def test_fetch_dart_quarterlies_subtracts_cumulative_to_single_quarter(monkeypatch):
    """H1/9M/FY 누적치 - 직전 누적 → 단일 분기."""
    _patch_fixed_kst(monkeypatch, year=2025)

    cumulative_by_period = {
        "11013": _make_extracted(100, 10, 8, 20, -15, -3),    # Q1
        "11012": _make_extracted(250, 28, 22, 50, -38, -8),   # H1 cumul
        "11014": _make_extracted(400, 48, 38, 80, -60, -13),  # 9M cumul
        "11011": _make_extracted(600, 75, 60, 120, -90, -20), # FY cumul
    }

    async def fake_fetch(corp, year, reprt_code, fs_div="CFS"):
        # 2025 만 응답, 그 외 빈 데이터
        if year != 2025:
            return _make_extracted()
        return cumulative_by_period.get(reprt_code, _make_extracted())

    monkeypatch.setattr(fs, "fetch_finstate_extracted", fake_fetch)

    fundamentals, cashflow = await fs._fetch_dart_quarterlies("005930")

    assert len(fundamentals) == 4
    assert [q["period"] for q in fundamentals] == ["2025 Q1", "2025 Q2", "2025 Q3", "2025 Q4"]
    assert fundamentals[0]["revenue"] == 100.0
    assert fundamentals[1]["revenue"] == 150.0  # H1 - Q1
    assert fundamentals[2]["revenue"] == 150.0  # 9M - H1
    assert fundamentals[3]["revenue"] == 200.0  # FY - 9M

    assert cashflow[0]["operatingCashFlow"] == 20.0
    assert cashflow[1]["operatingCashFlow"] == 30.0


@pytest.mark.asyncio
async def test_fetch_dart_quarterlies_recovers_n_minus_2_q4_when_prior_year_fy_missing(monkeypatch):
    """연초 시나리오 — 직전-1 Q4 까지 거슬러 quarters[-4:] 4개 확보."""
    _patch_fixed_kst(monkeypatch, year=2026, month=2, day=1)

    full_2024 = {
        "11013": _make_extracted(100, 10, 8, 20, -15, -3),
        "11012": _make_extracted(250, 28, 22, 50, -38, -8),
        "11014": _make_extracted(400, 48, 38, 80, -60, -13),
        "11011": _make_extracted(600, 75, 60, 120, -90, -20),
    }
    partial_2025 = {
        "11013": _make_extracted(200, 20, 16, 40, -30, -6),
        "11012": _make_extracted(500, 56, 44, 100, -76, -16),
        "11014": _make_extracted(800, 96, 76, 160, -120, -26),
    }

    async def fake_fetch(corp, year, reprt_code, fs_div="CFS"):
        if year == 2024:
            return full_2024.get(reprt_code, _make_extracted())
        if year == 2025:
            return partial_2025.get(reprt_code, _make_extracted())
        return _make_extracted()

    monkeypatch.setattr(fs, "fetch_finstate_extracted", fake_fetch)

    fundamentals, _ = await fs._fetch_dart_quarterlies("005930")
    periods = [q["period"] for q in fundamentals]
    assert periods == ["2024 Q4", "2025 Q1", "2025 Q2", "2025 Q3"]
    # 2024 Q4 = FY - 9M = 600 - 400 = 200
    assert fundamentals[0]["revenue"] == 200.0
    # 2025 Q1 = 200
    assert fundamentals[1]["revenue"] == 200.0


@pytest.mark.asyncio
async def test_fetch_dart_quarterlies_skips_quarter_when_prev_cumulative_missing(monkeypatch):
    """Q1 누락 시 Q2 (=H1-Q1) 계산 불가 → skip. Q3/Q4 는 정상."""
    _patch_fixed_kst(monkeypatch, year=2025)
    cumulative_by_period = {
        # 11013 (Q1) 누락
        "11012": _make_extracted(250, 28, 22, 50, -38, -8),
        "11014": _make_extracted(400, 48, 38, 80, -60, -13),
        "11011": _make_extracted(600, 75, 60, 120, -90, -20),
    }

    async def fake_fetch(corp, year, reprt_code, fs_div="CFS"):
        if year != 2025:
            return _make_extracted()
        return cumulative_by_period.get(reprt_code, _make_extracted())

    monkeypatch.setattr(fs, "fetch_finstate_extracted", fake_fetch)

    fundamentals, _ = await fs._fetch_dart_quarterlies("005930")
    periods = [q["period"] for q in fundamentals]
    assert "2025 Q1" not in periods
    assert "2025 Q2" not in periods
    assert "2025 Q3" in periods
    assert "2025 Q4" in periods


# ---------------------------------------------------------------------------
# _fetch_dart_annuals — FY 만 N 년치
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fetch_dart_annuals_returns_year_keyed_series(monkeypatch):
    """5년 연속 FY 데이터 → 5개 연도 시계열."""
    _patch_fixed_kst(monkeypatch, year=2025)

    annuals_by_year = {
        2020: _make_extracted(200, 20, 15, 40, -25, -8),
        2021: _make_extracted(220, 25, 18, 45, -28, -9),
        2022: _make_extracted(250, 30, 22, 50, -32, -10),
        2023: _make_extracted(280, 35, 26, 55, -36, -11),
        2024: _make_extracted(310, 40, 30, 60, -40, -12),
    }

    async def fake_fetch(corp, year, reprt_code, fs_div="CFS"):
        # FY (11011) 만 응답, 그 외 reprt_code 는 호출 안 됨
        assert reprt_code == "11011"
        return annuals_by_year.get(year, _make_extracted())

    monkeypatch.setattr(fs, "fetch_finstate_extracted", fake_fetch)

    fundamentals, cashflow = await fs._fetch_dart_annuals("005930", lookback_years=5)
    # 2020~2024 — 2025 는 _make_extracted() (모두 None) 이라 dropped
    periods = [r["period"] for r in fundamentals]
    assert periods == ["2020", "2021", "2022", "2023", "2024"]
    assert fundamentals[0]["revenue"] == 200.0
    assert fundamentals[-1]["netIncome"] == 30.0
    assert cashflow[2]["operatingCashFlow"] == 50.0


@pytest.mark.asyncio
async def test_fetch_dart_annuals_drops_years_with_no_data(monkeypatch):
    """미공시 연도는 자연스럽게 빠짐 — caller 가 별도 필터 안 해도 됨."""
    _patch_fixed_kst(monkeypatch, year=2025)

    async def fake_fetch(corp, year, reprt_code, fs_div="CFS"):
        if year in (2022, 2024):
            return _make_extracted(year * 1.0)
        return _make_extracted()  # all None

    monkeypatch.setattr(fs, "fetch_finstate_extracted", fake_fetch)

    fundamentals, _ = await fs._fetch_dart_annuals("005930", lookback_years=5)
    periods = [r["period"] for r in fundamentals]
    assert periods == ["2022", "2024"]


@pytest.mark.asyncio
async def test_fetch_dart_annuals_returns_empty_when_no_client(monkeypatch):
    async def no_dart(*_args, **_kwargs):
        return None
    monkeypatch.setattr(fs, "fetch_finstate_extracted", no_dart)

    fundamentals, cashflow = await fs._fetch_dart_annuals("005930")
    assert fundamentals == []
    assert cashflow == []

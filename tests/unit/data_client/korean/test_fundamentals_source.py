"""
Tests for KoreanFundamentalsSource (issue #42 Stage A+B).

External calls (yfinance fast_info, pykrx OHLCV) 은 module-level helper 로
분리해뒀으니 monkeypatch 로 deterministic mock. integration 검증 (실제 yf/pykrx
hit) 은 별도 (외부 의존이라 unit suite 에 포함 안 함).
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.data_client.korean import fundamentals_source as fs


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
# get_overview() — quote + performance 통합
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_overview_returns_quote_and_performance(monkeypatch):
    """yf + pykrx 정상 응답 시 quote + performance 합쳐진 dict 반환."""
    fake_quote = {
        "price": 75000.0,
        "change": 500.0,
        "changePct": 0.67,
        "open": 74500.0,
        "previousClose": 74500.0,
        "dayLow": 74400.0,
        "dayHigh": 75200.0,
        "yearLow": 60000.0,
        "yearHigh": 88000.0,
        "volume": 12_000_000.0,
        "marketCap": 500_000_000_000_000.0,
        "shares": 6_700_000_000.0,
        "pe": None,
        "eps": None,
    }
    fake_perf = {"1D": 0.67, "5D": 1.5, "1M": 3.2, "3M": -2.1, "6M": 5.0, "1Y": 25.0, "YTD": 10.0}

    monkeypatch.setattr(fs, "_fetch_quote_from_yf", lambda symbol: fake_quote)
    monkeypatch.setattr(fs, "_fetch_performance_from_pykrx", lambda ticker: fake_perf)

    source = fs.KoreanFundamentalsSource()
    result = await source.get_overview("005930.KS")

    assert result["symbol"] == "005930.KS"
    assert result["quote"] == fake_quote
    assert result["performance"] == fake_perf
    # 본 stage 에서 채우지 않는 필드는 None — frontend 가 빈 카드 안전 렌더
    assert result["analystRatings"] is None
    assert result["quarterlyFundamentals"] is None


@pytest.mark.asyncio
async def test_get_overview_yf_fail_returns_partial(monkeypatch):
    """yf 호출 실패 (빈 dict) 면 _partial=True — caller 가 unsupported 응답으로 fallback."""
    monkeypatch.setattr(fs, "_fetch_quote_from_yf", lambda symbol: {})
    monkeypatch.setattr(
        fs, "_fetch_performance_from_pykrx", lambda ticker: {"1D": 1.0}
    )

    source = fs.KoreanFundamentalsSource()
    result = await source.get_overview("005930.KS")

    assert result["_partial"] is True
    assert result["quote"] is None
    assert result["performance"] is None


# ---------------------------------------------------------------------------
# _fetch_performance_from_pykrx() — % 계산 logic
# ---------------------------------------------------------------------------

def _make_ohlcv_df(closes: list[float], freq: str = "B") -> pd.DataFrame:
    """Mock pykrx OHLCV DataFrame — '종가' column 만 채움 (계산에 충분)."""
    idx = pd.date_range(end="2026-04-27", periods=len(closes), freq=freq, name="날짜")
    return pd.DataFrame({"종가": closes}, index=idx)


def test_performance_computes_percentage_against_lookback(monkeypatch):
    # 마지막 종가 100, 1일 전 99, 5일 전 95 → 1D=1.01%, 5D=5.26%
    # 1Y lookback 은 252 영업일이라 총 260+ bars 필요.
    closes = [80.0] * 260 + [95.0, 96.0, 97.0, 98.0, 99.0, 100.0]
    monkeypatch.setattr(fs.stock, "get_market_ohlcv", lambda *a, **k: _make_ohlcv_df(closes))

    perf = fs._fetch_performance_from_pykrx("005930")
    assert perf["1D"] == pytest.approx((100 - 99) / 99 * 100, abs=0.01)
    assert perf["5D"] == pytest.approx((100 - 95) / 95 * 100, abs=0.01)
    assert perf["1Y"] is not None  # 252+ bars 있음


def test_performance_returns_none_when_insufficient_data(monkeypatch):
    # 짧은 시계열 — 1D 만 가능, 나머지는 None
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
    """pykrx schema 변경 / mock 결함으로 '종가' column 이 빠진 DataFrame 도 안전 처리.

    회귀 방지 — 직접 df["종가"] 인덱싱은 KeyError 로 /overview 전체 500 으로 번짐.
    """
    df_no_close = pd.DataFrame({"시가": [100.0, 101.0], "거래량": [10, 20]})
    monkeypatch.setattr(fs.stock, "get_market_ohlcv", lambda *a, **k: df_no_close)
    perf = fs._fetch_performance_from_pykrx("005930")
    assert all(v is None for v in perf.values())


# ---------------------------------------------------------------------------
# _safe_float — NaN / None / non-numeric 처리
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def test_valid_numeric(self):
        assert fs._safe_float(1.5) == 1.5
        assert fs._safe_float(0) == 0.0
        assert fs._safe_float("3.14") == 3.14

    def test_none_returns_none(self):
        assert fs._safe_float(None) is None

    def test_nan_returns_none(self):
        assert fs._safe_float(float("nan")) is None

    def test_non_numeric_returns_none(self):
        assert fs._safe_float("abc") is None
        assert fs._safe_float({}) is None

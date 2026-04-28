"""
Tests for KoreanFundamentalsSource (issue #42).

External calls (yfinance fast_info, pykrx OHLCV, DART finstate) 은 module-level
helper 로 분리해뒀으니 monkeypatch 로 deterministic mock. integration 검증
(실제 yf/pykrx/DART hit) 은 별도 — 외부 의존이라 unit suite 에 포함 안 함.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.data_client.korean import fundamentals_source as fs


@pytest.fixture(autouse=True)
def _reset_dart_singleton(monkeypatch):
    """DART 모듈 싱글톤은 테스트 격리 — 매 테스트 fresh state 로 시작."""
    monkeypatch.setattr(fs, "_dart_singleton", None)
    monkeypatch.setattr(fs, "_dart_init_attempted", False)


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
    # DART 미설정 시나리오 — fundamentals/cashFlow 빈 응답
    monkeypatch.setattr(fs, "_fetch_dart_quarterlies", lambda corp: ([], []))

    source = fs.KoreanFundamentalsSource()
    result = await source.get_overview("005930.KS")

    assert result["symbol"] == "005930.KS"
    assert result["quote"] == fake_quote
    assert result["performance"] == fake_perf
    # 본 PR 에서 채우지 않는 필드는 None — frontend 가 빈 카드 안전 렌더
    assert result["analystRatings"] is None
    assert result["quarterlyFundamentals"] is None
    assert result["cashFlow"] is None


@pytest.mark.asyncio
async def test_get_overview_strips_whitespace_from_symbol(monkeypatch):
    """공백 포함 호출 시 ticker / symbol_upper 모두 깨끗하게 정규화 — supports() 와 일관."""
    captured: dict[str, str] = {}

    def capture_quote(sym: str) -> dict[str, float]:
        captured["yf_symbol"] = sym
        return {"price": 1.0}

    def capture_perf(ticker: str) -> dict[str, float]:
        captured["pykrx_ticker"] = ticker
        return {"1D": 0.0}

    def capture_dart(corp: str) -> tuple[list, list]:
        captured["dart_corp"] = corp
        return ([], [])

    monkeypatch.setattr(fs, "_fetch_quote_from_yf", capture_quote)
    monkeypatch.setattr(fs, "_fetch_performance_from_pykrx", capture_perf)
    monkeypatch.setattr(fs, "_fetch_dart_quarterlies", capture_dart)

    source = fs.KoreanFundamentalsSource()
    result = await source.get_overview("  005930.KS  ")

    # symbol/ticker 모두 strip 됐는지 — 공백이 외부 호출까지 leak 안 돼야
    assert captured["yf_symbol"] == "005930.KS"
    assert captured["pykrx_ticker"] == "005930"
    assert captured["dart_corp"] == "005930"
    assert result["symbol"] == "005930.KS"


@pytest.mark.asyncio
async def test_get_overview_yf_fail_returns_partial(monkeypatch):
    """yf 호출 실패 (빈 dict) 면 _partial=True — caller 가 unsupported 응답으로 fallback."""
    monkeypatch.setattr(fs, "_fetch_quote_from_yf", lambda symbol: {})
    monkeypatch.setattr(
        fs, "_fetch_performance_from_pykrx", lambda ticker: {"1D": 1.0}
    )
    monkeypatch.setattr(fs, "_fetch_dart_quarterlies", lambda corp: ([], []))

    source = fs.KoreanFundamentalsSource()
    result = await source.get_overview("005930.KS")

    assert result["_partial"] is True
    assert result["quote"] is None
    assert result["performance"] is None


@pytest.mark.asyncio
async def test_get_overview_includes_dart_quarterly_data(monkeypatch):
    """DART 정상 응답 시 quarterlyFundamentals + cashFlow 가 overview 에 포함."""
    fundamentals = [
        {"period": "2025 Q3", "revenue": 80_000_000_000_000.0, "operatingIncome": 9_000_000_000_000.0, "netIncome": 7_000_000_000_000.0},
        {"period": "2025 Q4", "revenue": 85_000_000_000_000.0, "operatingIncome": 10_500_000_000_000.0, "netIncome": 8_000_000_000_000.0},
    ]
    cashflow = [
        {"period": "2025 Q3", "operatingCashFlow": 12_000_000_000_000.0, "investingCashFlow": -8_000_000_000_000.0, "financingCashFlow": -3_000_000_000_000.0},
        {"period": "2025 Q4", "operatingCashFlow": 14_000_000_000_000.0, "investingCashFlow": -9_500_000_000_000.0, "financingCashFlow": -3_500_000_000_000.0},
    ]
    monkeypatch.setattr(fs, "_fetch_quote_from_yf", lambda symbol: {"price": 75000.0})
    monkeypatch.setattr(fs, "_fetch_performance_from_pykrx", lambda ticker: {"1D": 0.5})
    monkeypatch.setattr(fs, "_fetch_dart_quarterlies", lambda corp: (fundamentals, cashflow))

    source = fs.KoreanFundamentalsSource()
    result = await source.get_overview("005930.KS")

    assert result["quarterlyFundamentals"] == fundamentals
    assert result["cashFlow"] == cashflow


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


# ---------------------------------------------------------------------------
# _extract_dart_amount — DART finstate row 매칭
# ---------------------------------------------------------------------------


def _make_dart_df(rows: list[dict]) -> pd.DataFrame:
    """DART finstate-shape DataFrame helper."""
    return pd.DataFrame(rows)


class TestExtractDartAmount:
    def test_account_id_match_takes_priority(self):
        # account_id 와 account_nm 둘 다 있을 때 account_id 매칭 우선.
        df = _make_dart_df([
            {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": "200"},
            {"sj_div": "IS", "account_id": "dart_OtherSales", "account_nm": "기타매출", "thstrm_amount": "999"},
        ])
        assert fs._extract_dart_amount(df, fs._REVENUE_IDS, fs._REVENUE_KEYWORDS) == 200.0

    def test_falls_back_to_account_nm_when_no_id_match(self):
        # account_id 매칭 실패 → account_nm substring 으로 fallback (K-GAAP 회사 등).
        df = _make_dart_df([
            {"sj_div": "IS", "account_id": "custom_kgaap_revenue", "account_nm": "매출액", "thstrm_amount": "150"},
        ])
        assert fs._extract_dart_amount(df, fs._REVENUE_IDS, fs._REVENUE_KEYWORDS) == 150.0

    def test_substring_match_alternate_revenue_label(self):
        df = _make_dart_df([
            {"sj_div": "IS", "account_id": "x", "account_nm": "영업수익", "thstrm_amount": "300"},
        ])
        assert fs._extract_dart_amount(df, fs._REVENUE_IDS, fs._REVENUE_KEYWORDS) == 300.0

    def test_returns_none_when_no_match(self):
        df = _make_dart_df([
            {"sj_div": "IS", "account_id": "x", "account_nm": "기타수익", "thstrm_amount": "50"},
        ])
        assert fs._extract_dart_amount(df, fs._REVENUE_IDS, fs._REVENUE_KEYWORDS) is None

    def test_filters_by_sj_div(self):
        # CF 에서 영업이익 키워드 — IS 에만 한정하면 None
        df = _make_dart_df([
            {"sj_div": "CF", "account_id": "x", "account_nm": "영업이익", "thstrm_amount": "999"},
        ])
        assert fs._extract_dart_amount(df, (), fs._OPERATING_INCOME_KEYWORDS, sj_divs=("IS",)) is None
        assert fs._extract_dart_amount(df, (), fs._OPERATING_INCOME_KEYWORDS, sj_divs=("CF",)) == 999.0

    def test_handles_comma_formatted_amounts(self):
        # finstate (legacy) 는 thstrm_amount 가 천단위 콤마 string
        df = _make_dart_df([
            {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": "79,140,503,000,000"},
        ])
        assert fs._extract_dart_amount(df, fs._REVENUE_IDS, fs._REVENUE_KEYWORDS) == 79_140_503_000_000.0

    def test_handles_missing_columns_safely(self):
        # account_nm / account_id column 누락 — KeyError 대신 None
        df = pd.DataFrame({"sj_div": ["IS"], "thstrm_amount": ["100"]})
        assert fs._extract_dart_amount(df, fs._REVENUE_IDS, fs._REVENUE_KEYWORDS) is None

    def test_handles_empty_dataframe(self):
        assert fs._extract_dart_amount(pd.DataFrame(), fs._REVENUE_IDS, fs._REVENUE_KEYWORDS) is None
        assert fs._extract_dart_amount(None, fs._REVENUE_IDS, fs._REVENUE_KEYWORDS) is None  # type: ignore[arg-type]

    def test_handles_dict_response_from_dart_no_data(self):
        # OpenDartReader 가 'no data' 시 dict 반환하는 케이스 — DataFrame 아님 → None
        no_data = {"status": "013", "message": "조회된 데이타가 없습니다."}
        assert fs._extract_dart_amount(no_data, fs._REVENUE_IDS, fs._REVENUE_KEYWORDS) is None  # type: ignore[arg-type]

    def test_prefers_thstrm_add_amount_for_is_rows(self):
        """IS rows: thstrm_amount 는 단일 분기 (3M), thstrm_add_amount 는 연초 누적.

        회귀 방지 — H1 보고서의 thstrm_amount (74,566억) 를 잘못 누적치로 쓰면
        Q2 = H1 - Q1 = 음수가 나옴. 누적치 (thstrm_add_amount=153,706억) 우선.
        """
        df = _make_dart_df([{
            "sj_div": "IS",
            "account_id": "ifrs-full_Revenue",
            "account_nm": "매출액",
            "thstrm_amount": "74,566,317,000,000",
            "thstrm_add_amount": "153,706,820,000,000",
        }])
        # add_amount = H1 cumulative — 누적치 우선
        assert fs._extract_dart_amount(df, fs._REVENUE_IDS, fs._REVENUE_KEYWORDS) == 153_706_820_000_000.0

    def test_falls_back_to_thstrm_amount_when_add_empty(self):
        """FY 보고서 / Q1 보고서 / CF rows: thstrm_add_amount 비어있음 → amount 가 누적."""
        df = _make_dart_df([{
            "sj_div": "IS",
            "account_id": "ifrs-full_Revenue",
            "account_nm": "매출액",
            "thstrm_amount": "333,605,938,000,000",
            "thstrm_add_amount": "",  # FY report — add 비어있음
        }])
        assert fs._extract_dart_amount(df, fs._REVENUE_IDS, fs._REVENUE_KEYWORDS) == 333_605_938_000_000.0

        df_cf = _make_dart_df([{
            "sj_div": "CF",
            "account_id": "ifrs-full_CashFlowsFromUsedInOperatingActivities",
            "account_nm": "영업활동현금흐름",
            "thstrm_amount": "33,941,002,000,000",
            "thstrm_add_amount": float("nan"),
        }])
        assert fs._extract_dart_amount(df_cf, fs._OPERATING_CF_IDS, fs._OPERATING_CF_KEYWORDS, sj_divs=("CF",)) == 33_941_002_000_000.0


# ---------------------------------------------------------------------------
# _fetch_dart_quarterlies — 누적 → 분기 차분 + DART 가용성 분기
# ---------------------------------------------------------------------------


def _build_finstate_df(
    revenue_cum: float, op_cum: float, net_cum: float,
    op_cf_cum: float, inv_cf_cum: float, fin_cf_cum: float,
) -> pd.DataFrame:
    """누적 금액으로 채운 DART finstate_all-shape DataFrame.

    finstate_all 은 한 호출당 단일 fs_div ("CFS" 또는 "OFS") — fs_div column 없음.
    account_id (XBRL 표준 ID) 와 account_nm 둘 다 채워서 매칭 fallback 검증.
    """
    return _make_dart_df([
        {"sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": str(revenue_cum)},
        {"sj_div": "IS", "account_id": "dart_OperatingIncomeLoss", "account_nm": "영업이익", "thstrm_amount": str(op_cum)},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLoss", "account_nm": "분기순이익", "thstrm_amount": str(net_cum)},
        {"sj_div": "CF", "account_id": "ifrs-full_CashFlowsFromUsedInOperatingActivities", "account_nm": "영업활동현금흐름", "thstrm_amount": str(op_cf_cum)},
        {"sj_div": "CF", "account_id": "ifrs-full_CashFlowsFromUsedInInvestingActivities", "account_nm": "투자활동현금흐름", "thstrm_amount": str(inv_cf_cum)},
        {"sj_div": "CF", "account_id": "ifrs-full_CashFlowsFromUsedInFinancingActivities", "account_nm": "재무활동현금흐름", "thstrm_amount": str(fin_cf_cum)},
    ])


def test_fetch_dart_quarterlies_returns_empty_when_no_client(monkeypatch):
    """DART_API_KEY 미설정 (client=None) 이면 ([], []) — overview 는 정상 응답 유지."""
    monkeypatch.setattr(fs, "_get_dart_client", lambda: None)
    fundamentals, cashflow = fs._fetch_dart_quarterlies("005930")
    assert fundamentals == []
    assert cashflow == []


def test_fetch_dart_quarterlies_subtracts_cumulative_to_single_quarter(monkeypatch):
    """H1/9M/FY 의 누적치를 직전 보고서 빼서 단일 분기 환산."""
    # 한 해 (year=2025) full data — Q1=100, H1=250 → Q2=150, 9M=400 → Q3=150, FY=600 → Q4=200
    cumulative_by_period = {
        "11013": _build_finstate_df(100, 10, 8, 20, -15, -3),    # Q1
        "11012": _build_finstate_df(250, 28, 22, 50, -38, -8),   # H1 cumul
        "11014": _build_finstate_df(400, 48, 38, 80, -60, -13),  # 9M cumul
        "11011": _build_finstate_df(600, 75, 60, 120, -90, -20), # FY cumul
    }
    fake_dart = MagicMock()
    # year=2024 (전년) 은 None — 조회는 시도되지만 빈 응답
    # year=2025 (당해) 만 응답.

    def finstate(corp, year, reprt_code):
        if year != 2025:
            return None
        return cumulative_by_period.get(reprt_code)

    fake_dart.finstate_all = finstate
    monkeypatch.setattr(fs, "_get_dart_client", lambda: fake_dart)
    # _KST.now() 의 year 로 fetch 범위 결정 — 2025 가 포함되도록 stub
    import datetime as _dt
    class FixedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2025, 12, 31, tzinfo=tz)
    monkeypatch.setattr(fs, "datetime", FixedDatetime)

    fundamentals, cashflow = fs._fetch_dart_quarterlies("005930")

    # 4 quarters expected
    assert len(fundamentals) == 4
    assert [q["period"] for q in fundamentals] == ["2025 Q1", "2025 Q2", "2025 Q3", "2025 Q4"]
    # Q1 = cumulative
    assert fundamentals[0]["revenue"] == 100.0
    # Q2 = H1 - Q1 = 250 - 100 = 150
    assert fundamentals[1]["revenue"] == 150.0
    # Q3 = 9M - H1 = 400 - 250 = 150
    assert fundamentals[2]["revenue"] == 150.0
    # Q4 = FY - 9M = 600 - 400 = 200
    assert fundamentals[3]["revenue"] == 200.0

    # cashflow 도 동일 로직으로 차분
    assert cashflow[0]["operatingCashFlow"] == 20.0
    assert cashflow[1]["operatingCashFlow"] == 30.0  # 50 - 20


def test_fetch_dart_quarterlies_skips_quarter_when_prev_cumulative_missing(monkeypatch):
    """Q1 보고서 누락 시 Q2 (=H1-Q1) 계산 불가 → skip. Q3/Q4 는 정상."""
    cumulative_by_period = {
        # 11013 (Q1) 누락 — Q2 계산 불가
        "11012": _build_finstate_df(250, 28, 22, 50, -38, -8),
        "11014": _build_finstate_df(400, 48, 38, 80, -60, -13),
        "11011": _build_finstate_df(600, 75, 60, 120, -90, -20),
    }
    fake_dart = MagicMock()
    fake_dart.finstate_all = lambda corp, year, reprt_code: (
        cumulative_by_period.get(reprt_code) if year == 2025 else None
    )
    monkeypatch.setattr(fs, "_get_dart_client", lambda: fake_dart)

    import datetime as _dt
    class FixedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2025, 12, 31, tzinfo=tz)
    monkeypatch.setattr(fs, "datetime", FixedDatetime)

    fundamentals, _ = fs._fetch_dart_quarterlies("005930")

    # Q1 자체도 없고 Q2 도 prev 없어서 skip — Q3/Q4 만 (단, Q3 도 H1 가 prev — H1 있으니 가능)
    periods = [q["period"] for q in fundamentals]
    assert "2025 Q1" not in periods
    assert "2025 Q2" not in periods
    assert "2025 Q3" in periods
    assert "2025 Q4" in periods


def test_fetch_dart_quarterlies_handles_finstate_exception(monkeypatch):
    """finstate 가 예외 raise 해도 다른 보고서 fetch 는 계속 — 일부 분기만 나옴."""
    fake_dart = MagicMock()

    def flaky_finstate(corp, year, reprt_code):
        if reprt_code == "11013":
            raise RuntimeError("DART rate limit")
        if year == 2025 and reprt_code == "11012":
            return _build_finstate_df(250, 28, 22, 50, -38, -8)
        if year == 2025 and reprt_code == "11014":
            return _build_finstate_df(400, 48, 38, 80, -60, -13)
        return None

    fake_dart.finstate_all = flaky_finstate
    monkeypatch.setattr(fs, "_get_dart_client", lambda: fake_dart)

    import datetime as _dt
    class FixedDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2025, 12, 31, tzinfo=tz)
    monkeypatch.setattr(fs, "datetime", FixedDatetime)

    fundamentals, _ = fs._fetch_dart_quarterlies("005930")
    # Q1 raise → cumulative 누락 → Q2 skip; 9M 은 H1 prev 있으니 Q3 만 나옴
    periods = [q["period"] for q in fundamentals]
    assert periods == ["2025 Q3"]


def test_get_dart_client_returns_none_without_api_key(monkeypatch):
    """DART_API_KEY 환경변수 없으면 client = None — 모듈 import 시 raise 되지 않음."""
    monkeypatch.delenv("DART_API_KEY", raising=False)
    # autouse fixture 가 singleton reset 해줌
    assert fs._get_dart_client() is None
    # 두 번째 호출도 None — init_attempted flag 동작
    assert fs._get_dart_client() is None

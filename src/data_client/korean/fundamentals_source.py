"""KR fundamentals — quote / performance / quarterly financials for KOSPI/KOSDAQ.

FORK (#42):
* Quote (시가총액 / 52W / dayHigh-Low / shares): yfinance ``fast_info`` 사용.
  pykrx 의 ``get_market_fundamental`` / ``get_market_cap`` 은 KRX 사이트 응답
  구조 변경으로 현재 KeyError 발생 — yfinance fast_info 가 KR ticker 도 안정적
  으로 처리.
* Performance (1D / 5D / 1M / 3M / 6M / 1Y / YTD %): pykrx OHLCV 로 계산.
* Quarterly fundamentals (revenue / operating income / net income) +
  cashFlow (operating / investing / financing): DART finstate 누적 보고서
  4개 (Q1 / H1 / 9M / FY) 를 가져와 차분으로 단일 분기 환산. ``DART_API_KEY``
  미설정·요청 실패 시 None 으로 fallback — quote/performance 는 정상 응답.
* PER / PBR / EPS / 배당 / analystRatings / revenueByProduct·Geo: 별도 issue.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf
from pykrx import stock

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")
_KR_SUFFIXES = (".KS", ".KQ")

# Performance bar 가 채울 기간 (영업일 lookback). YTD 는 별도 — 연초부터.
_PERIOD_BUSINESS_DAYS: dict[str, int] = {
    "1D": 1,
    "5D": 5,
    "1M": 22,    # 월 평균 거래일
    "3M": 66,
    "6M": 132,
    "1Y": 252,
}

# DART 보고서 코드 — Q1=11013, H1=11012(누적), 3Q=11014(누적), FY=11011(누적).
# H1·9M·FY 는 누적치이므로 단일 분기 환산은 차분(subtraction) 필요.
_DART_REPRT_CODES: list[tuple[str, str]] = [
    ("Q1", "11013"),
    ("H1", "11012"),
    ("9M", "11014"),
    ("FY", "11011"),
]
_DART_PREV_LABEL = {"H1": "Q1", "9M": "H1", "FY": "9M"}
_DART_QUARTER_LABEL = {"Q1": "Q1", "H1": "Q2", "9M": "Q3", "FY": "Q4"}

# 계정 매칭 — 우선 XBRL ``account_id`` (IFRS/DART 표준 ID, 회사간 안정) 으로
# 매칭하고, 누락 시 ``account_nm`` substring 매칭으로 fallback (K-GAAP 등).
# Tuple 안 순서 = 우선순위.
_REVENUE_IDS = (
    "ifrs-full_Revenue",
    "ifrs-full_RevenueFromContractsWithCustomers",
)
_REVENUE_KEYWORDS = ("매출액", "수익(매출액)", "영업수익")

_OPERATING_INCOME_IDS = ("dart_OperatingIncomeLoss",)
_OPERATING_INCOME_KEYWORDS = ("영업이익",)

_NET_INCOME_IDS = ("ifrs-full_ProfitLoss",)
_NET_INCOME_KEYWORDS = ("당기순이익", "분기순이익", "반기순이익")

_OPERATING_CF_IDS = ("ifrs-full_CashFlowsFromUsedInOperatingActivities",)
_OPERATING_CF_KEYWORDS = ("영업활동현금흐름", "영업활동으로 인한 현금흐름")

_INVESTING_CF_IDS = ("ifrs-full_CashFlowsFromUsedInInvestingActivities",)
_INVESTING_CF_KEYWORDS = ("투자활동현금흐름", "투자활동으로 인한 현금흐름")

_FINANCING_CF_IDS = ("ifrs-full_CashFlowsFromUsedInFinancingActivities",)
_FINANCING_CF_KEYWORDS = ("재무활동현금흐름", "재무활동으로 인한 현금흐름")


def _strip_suffix(symbol: str) -> str:
    upper = symbol.upper()
    for suffix in _KR_SUFFIXES:
        if upper.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


def _safe_float(value: Any) -> float | None:
    """Coerce to float. None for NaN / None / non-numeric.

    DART finstate 의 ``thstrm_amount`` 는 천단위 콤마 string ("79,140,503,000,000")
    이므로 strip 후 파싱.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = value.replace(",", "").strip()
        if not value or value == "-":
            return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _fetch_quote_from_yf(yahoo_symbol: str) -> dict[str, Any]:
    """yfinance fast_info 로 quote 도출. 호출 실패 시 빈 dict (caller 가 partial 응답 OK)."""
    try:
        ticker = yf.Ticker(yahoo_symbol)
        fi = ticker.fast_info
        last = _safe_float(fi.get("lastPrice"))
        prev = _safe_float(fi.get("previousClose"))
        change = (last - prev) if (last is not None and prev is not None and prev != 0) else None
        change_pct = (change / prev * 100) if (change is not None and prev) else None
        shares = _safe_float(fi.get("shares"))
        return {
            "price": last,
            "change": change,
            "changePct": change_pct,
            "open": _safe_float(fi.get("open")),
            "previousClose": prev,
            "dayLow": _safe_float(fi.get("dayLow")),
            "dayHigh": _safe_float(fi.get("dayHigh")),
            "yearLow": _safe_float(fi.get("yearLow")),
            "yearHigh": _safe_float(fi.get("yearHigh")),
            "volume": _safe_float(fi.get("lastVolume")),
            "marketCap": _safe_float(fi.get("marketCap")),
            "shares": shares,
            # 본 PR 에서 채우지 않음 — DART/yf info 통합 별도 PR.
            "pe": None,
            "eps": None,
        }
    except Exception:
        logger.warning("kr_fundamentals.yf_quote.failed | symbol=%s", yahoo_symbol, exc_info=True)
        return {}


def _fetch_performance_from_pykrx(ticker: str) -> dict[str, float | None]:
    """pykrx OHLCV 로 7개 period % 계산. 거래일 부족 시 해당 period 만 None."""
    today_kst = datetime.now(_KST)
    # 1Y + 여유 30일 lookback — 252 영업일 + 휴일 buffer.
    start_date = today_kst - timedelta(days=int(252 * 1.6))
    start = start_date.strftime("%Y%m%d")
    end = today_kst.strftime("%Y%m%d")

    try:
        df = stock.get_market_ohlcv(start, end, ticker)
    except Exception:
        logger.warning("kr_fundamentals.pykrx_ohlcv.failed | ticker=%s", ticker, exc_info=True)
        return {p: None for p in (*_PERIOD_BUSINESS_DAYS.keys(), "YTD")}

    # FORK (#42): "종가" column 누락 (pykrx 응답 schema 변경 / mock 결함) 도 빈 응답으로
    # 안전 처리 — 직접 인덱싱은 KeyError 로 /overview 전체 500 으로 번질 수 있음.
    if df is None or df.empty or "종가" not in df.columns:
        return {p: None for p in (*_PERIOD_BUSINESS_DAYS.keys(), "YTD")}

    closes = df["종가"]
    last_close = _safe_float(closes.iloc[-1])
    if last_close is None or last_close == 0:
        return {p: None for p in (*_PERIOD_BUSINESS_DAYS.keys(), "YTD")}

    perf: dict[str, float | None] = {}
    for label, lookback_bd in _PERIOD_BUSINESS_DAYS.items():
        # iloc[-1] 이 today, 그 이전 lookback_bd 번째 종가와 비교.
        if len(closes) <= lookback_bd:
            perf[label] = None
            continue
        ref = _safe_float(closes.iloc[-1 - lookback_bd])
        if ref is None or ref == 0:
            perf[label] = None
            continue
        perf[label] = round((last_close - ref) / ref * 100, 4)

    # YTD: 올해 첫 거래일 종가 기준.
    year = today_kst.year
    ytd_mask = df.index.year == year
    if ytd_mask.any():
        ytd_first = _safe_float(df[ytd_mask]["종가"].iloc[0])
        perf["YTD"] = (
            round((last_close - ytd_first) / ytd_first * 100, 4)
            if ytd_first
            else None
        )
    else:
        perf["YTD"] = None

    return perf


# ---------------------------------------------------------------------------
# DART 분기 재무제표 — quarterlyFundamentals + cashFlow
# ---------------------------------------------------------------------------

# OpenDartReader 인스턴스는 corp_code XML(~5MB) 을 첫 호출에 download → 메모리 캐시.
# /overview latency 보호 위해 모듈 싱글톤 — DART_API_KEY 미설정 시 None 반환.
# Lock + double-checked init: 동시 /overview 요청이 to_thread 에서 동시에 들어오면
# init 중인 thread 외 다른 thread 가 init_attempted=True 만 보고 미완성 None 받는
# race 방지 — 첫 thread 가 OpenDartReader instantiate (수초 소요) 끝낼 때까지 wait.
_dart_singleton: Any | None = None
_dart_init_attempted: bool = False
_dart_init_lock = threading.Lock()


def _get_dart_client() -> Any | None:
    """Lazy singleton OpenDartReader. ``None`` if API key missing or import fails."""
    global _dart_singleton, _dart_init_attempted
    if _dart_init_attempted:
        return _dart_singleton
    with _dart_init_lock:
        # double-check: 다른 thread 가 lock 안에서 이미 init 끝냈을 수 있음.
        if _dart_init_attempted:
            return _dart_singleton
        api_key = os.getenv("DART_API_KEY")
        if not api_key:
            logger.info("kr_fundamentals.dart.no_api_key")
            _dart_singleton = None
        else:
            try:
                import OpenDartReader  # noqa: N813 — package name is camelCase
                _dart_singleton = OpenDartReader(api_key)
            except Exception:
                logger.warning("kr_fundamentals.dart.client_init.failed", exc_info=True)
                _dart_singleton = None
        # init 완전히 끝난 후 flag — 미완성 상태 노출 안 됨.
        _dart_init_attempted = True
    return _dart_singleton


def _row_cumulative_amount(row: pd.Series) -> float | None:
    """누적치 추출 — DART finstate_all 의 sj_div 별 컬럼 의미 차이 정규화.

    * IS (분기보고서) : ``thstrm_amount`` = 해당 분기 단독 (3M),
      ``thstrm_add_amount`` = 연초부터 누적. → add_amount 우선.
    * IS (사업보고서/Q1) : ``thstrm_add_amount`` 빈 값 → amount 가 곧 누적.
    * CF / BS : ``thstrm_add_amount`` 가 NaN → amount 사용.

    이 정규화로 모든 row 가 "연초부터 누적" 의미를 갖게 되어 차분 logic 단순화.
    """
    # add_amount = 0 도 valid (해당 분기 누적이 정확히 0 인 항목 — 드물지만 가능).
    # `_safe_float` 가 None / NaN / 빈 문자열을 None 으로 정규화하므로
    # is-not-None 만으로 missing 판정 충분.
    add_val = _safe_float(row.get("thstrm_add_amount"))
    if add_val is not None:
        return add_val
    return _safe_float(row.get("thstrm_amount"))


def _extract_dart_amount(
    df: pd.DataFrame,
    account_ids: tuple[str, ...] = (),
    account_keywords: tuple[str, ...] = (),
    sj_divs: tuple[str, ...] = ("IS", "CIS", "CF"),
) -> float | None:
    """DART finstate_all DataFrame 에서 첫 매칭 row 의 누적금액 반환.

    매칭 우선순위:
    1. ``account_id`` exact match (IFRS / DART 표준 ID — 회사간 안정)
    2. ``account_nm`` substring match (XBRL ID 없는 K-GAAP 회사 fallback)

    값 추출은 ``_row_cumulative_amount`` — IS / CF schema 차이 정규화.
    schema 변경 / 누락 column 모두 None 으로 안전 처리 — 직접 인덱싱은
    KeyError 로 /overview 전체 500 으로 번질 수 있음.
    """
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return None
    if "thstrm_amount" not in df.columns:
        return None

    candidates = df[df["sj_div"].isin(sj_divs)] if "sj_div" in df.columns else df
    # finstate (legacy) 는 fs_div column 으로 CFS/OFS 혼재 — CFS 우선.
    # finstate_all 은 한 호출당 한 fs_div 라 column 자체가 없음.
    if "fs_div" in candidates.columns:
        cfs = candidates[candidates["fs_div"] == "CFS"]
        if not cfs.empty:
            candidates = cfs

    if account_ids and "account_id" in candidates.columns:
        for aid in account_ids:
            matches = candidates[candidates["account_id"] == aid]
            if not matches.empty:
                return _row_cumulative_amount(matches.iloc[0])

    if account_keywords and "account_nm" in candidates.columns:
        for kw in account_keywords:
            for _, row in candidates.iterrows():
                if kw in str(row.get("account_nm", "")):
                    return _row_cumulative_amount(row)
    return None


def _fetch_dart_quarterlies(
    stock_code: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """DART 분기 재무로 (quarterlyFundamentals, cashFlow) 도출. 최근 4개 분기.

    DART 의 H1/9M/FY thstrm_amount 는 누적치 — 직전 누적 빼서 단일 분기 환산.
    직전 보고서가 없으면 (예: H1 만 공시되고 Q1 누락) 해당 분기 skip.

    Returns
    -------
    fundamentals : list[{period, revenue, operatingIncome, netIncome}]
    cashflow : list[{period, operatingCashFlow, investingCashFlow, financingCashFlow}]

    DART 미설정 / 모든 보고서 fetch 실패 시 ``([], [])``.
    """
    dart = _get_dart_client()
    if dart is None:
        return [], []

    today = datetime.now(_KST)
    # 직전 2년 + 당해 — 연초 (예: 1~3월) 에는 직전 해 FY 미공시라 (마감 90일 전)
    # 직전-1 의 Q4 까지 거슬러 올라가야 quarters[-4:] 가 4개 채워짐.
    years = (today.year - 2, today.year - 1, today.year)

    cumulative: dict[tuple[int, str], dict[str, float | None]] = {}
    for year in years:
        for label, reprt_code in _DART_REPRT_CODES:
            try:
                # finstate (key items) 는 CF rows 가 없어서 cashFlow 채울 수 없음.
                # finstate_all 은 XBRL 전체 항목 + account_id 까지 포함 — IFRS 표준 ID
                # 매칭으로 회사간 robust. 기본 fs_div='CFS' (연결재무제표).
                df = dart.finstate_all(stock_code, year, reprt_code=reprt_code)
            except Exception:
                logger.warning(
                    "kr_fundamentals.dart.finstate_all.failed | corp=%s year=%s reprt=%s",
                    stock_code, year, reprt_code, exc_info=True,
                )
                continue
            if df is None or not isinstance(df, pd.DataFrame) or df.empty:
                continue
            cumulative[(year, label)] = {
                "revenue": _extract_dart_amount(df, _REVENUE_IDS, _REVENUE_KEYWORDS, sj_divs=("IS", "CIS")),
                "operatingIncome": _extract_dart_amount(df, _OPERATING_INCOME_IDS, _OPERATING_INCOME_KEYWORDS, sj_divs=("IS", "CIS")),
                "netIncome": _extract_dart_amount(df, _NET_INCOME_IDS, _NET_INCOME_KEYWORDS, sj_divs=("IS", "CIS")),
                "operatingCashFlow": _extract_dart_amount(df, _OPERATING_CF_IDS, _OPERATING_CF_KEYWORDS, sj_divs=("CF",)),
                "investingCashFlow": _extract_dart_amount(df, _INVESTING_CF_IDS, _INVESTING_CF_KEYWORDS, sj_divs=("CF",)),
                "financingCashFlow": _extract_dart_amount(df, _FINANCING_CF_IDS, _FINANCING_CF_KEYWORDS, sj_divs=("CF",)),
            }

    if not cumulative:
        return [], []

    quarters: list[dict[str, Any]] = []
    period_sequence = [(y, lbl) for y in sorted(years) for lbl, _ in _DART_REPRT_CODES]
    for year, label in period_sequence:
        cum = cumulative.get((year, label))
        if cum is None:
            continue
        if label == "Q1":
            single = dict(cum)
        else:
            prev = cumulative.get((year, _DART_PREV_LABEL[label]))
            if prev is None:
                continue
            single = {
                k: (cum[k] - prev[k]) if (cum[k] is not None and prev[k] is not None) else None
                for k in cum
            }
        period = f"{year} {_DART_QUARTER_LABEL[label]}"
        quarters.append({"period": period, **single})

    quarters = quarters[-4:]

    fundamentals = [
        {
            "period": q["period"],
            "revenue": q["revenue"],
            "operatingIncome": q["operatingIncome"],
            "netIncome": q["netIncome"],
        }
        for q in quarters
    ]
    cashflow = [
        {
            "period": q["period"],
            "operatingCashFlow": q["operatingCashFlow"],
            "investingCashFlow": q["investingCashFlow"],
            "financingCashFlow": q["financingCashFlow"],
        }
        for q in quarters
    ]
    return fundamentals, cashflow


# ---------------------------------------------------------------------------
# Source class
# ---------------------------------------------------------------------------


class KoreanFundamentalsSource:
    """quote + performance + quarterly fundamentals/cashFlow for KR ticker (.KS / .KQ).

    revenueByProduct / revenueByGeo / earningsSurprises / analyst consensus 는
    별도 source — XBRL 본문 파싱 (segment) / 별도 컨센서스 데이터 필요.
    """

    @staticmethod
    def supports(symbol: str) -> bool:
        # is_unsupported_analyst_market / _is_kr_symbol 와 동일하게 strip + upper 정규화.
        return symbol.strip().upper().endswith(_KR_SUFFIXES)

    async def get_overview(self, symbol: str) -> dict[str, Any]:
        """Return CompanyOverviewResponse 호환 dict (KR 채울 수 있는 부분만).

        외부 호출 (yfinance + pykrx + DART) 은 ``asyncio.to_thread`` 로 wrap +
        ``asyncio.gather`` 로 병렬. DART 미설정·실패는 fundamentals/cashFlow
        만 None 으로 graceful degrade — quote/performance 응답은 정상.
        """
        # FORK (#42): supports() / is_unsupported_analyst_market 와 동일하게 strip 먼저.
        # 호출자가 이미 normalize 했더라도 source 자체가 raw input 안전 처리.
        symbol = symbol.strip()
        ticker = _strip_suffix(symbol)
        symbol_upper = symbol.upper()

        quote, performance, dart_data = await asyncio.gather(
            asyncio.to_thread(_fetch_quote_from_yf, symbol_upper),
            asyncio.to_thread(_fetch_performance_from_pykrx, ticker),
            asyncio.to_thread(_fetch_dart_quarterlies, ticker),
        )
        fundamentals, cashflow = dart_data

        # quote 가 비었으면 unsupported 와 동일 — caller 가 unsupported flag 다시 세움.
        if not quote:
            return {
                "symbol": symbol_upper,
                "name": None,
                "quote": None,
                "performance": None,
                "_partial": True,
            }

        # name 은 yfinance fast_info 에 없음. Ticker.info 는 비싸서 생략.
        return {
            "symbol": symbol_upper,
            "name": None,
            "quote": quote,
            "performance": performance,
            "quarterlyFundamentals": fundamentals or None,
            "cashFlow": cashflow or None,
            # 별도 issue — KR 컨센서스 source / XBRL segment 파싱 필요.
            "analystRatings": None,
            "earningsSurprises": None,
            "revenueByProduct": None,
            "revenueByGeo": None,
        }

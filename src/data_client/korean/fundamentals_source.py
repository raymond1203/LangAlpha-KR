"""KR fundamentals — quote / performance / quarterly + annual financials for KOSPI/KOSDAQ.

FORK (#42):
* Quote (시가총액 / 52W / dayHigh-Low / shares): yfinance ``fast_info`` 사용.
  pykrx 의 ``get_market_fundamental`` / ``get_market_cap`` 은 KRX 사이트 응답
  구조 변경으로 현재 KeyError 발생 — yfinance fast_info 가 KR ticker 도 안정적
  으로 처리.
* Performance (1D / 5D / 1M / 3M / 6M / 1Y / YTD %): pykrx OHLCV 로 계산.
* Quarterly fundamentals + cashFlow: DART finstate 누적 보고서 4개
  (Q1 / H1 / 9M / FY) 를 가져와 차분으로 단일 분기 환산.
* Annual fundamentals + cashFlow (#52): DART FY (11011) 만 N 년치 가져와
  연도별 시계열로 노출. 5년 trend 쿼리가 5×4 → 5×1 호출로 절감.
* DART 호출은 ``dart_client.fetch_finstate_extracted`` 경유 — Redis 캐시 +
  exponential backoff 재시도. ``DART_API_KEY`` 미설정·요청 실패 시 None.
* PER / PBR / EPS / 배당 / analystRatings / revenueByProduct·Geo: 별도 issue.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import yfinance as yf
from pykrx import stock

from src.data_client.korean.dart_client import fetch_finstate_extracted

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
_DART_REPRT_CODES: list[tuple[str, str]] = [
    ("Q1", "11013"),
    ("H1", "11012"),
    ("9M", "11014"),
    ("FY", "11011"),
]
_DART_PREV_LABEL = {"H1": "Q1", "9M": "H1", "FY": "9M"}
_DART_QUARTER_LABEL = {"Q1": "Q1", "H1": "Q2", "9M": "Q3", "FY": "Q4"}

# Annual lookback default — 5년 전부터 직전 FY 까지. 요청 시 confirm 안 된 데이터
# (당해 FY 미공시) 는 자연스럽게 빠지므로 caller 가 별도 필터 안 해도 됨.
_ANNUAL_LOOKBACK_YEARS = 5

_FUNDAMENTAL_KEYS = ("revenue", "operatingIncome", "netIncome")
_CASHFLOW_KEYS = ("operatingCashFlow", "investingCashFlow", "financingCashFlow")


def _strip_suffix(symbol: str) -> str:
    upper = symbol.upper()
    for suffix in _KR_SUFFIXES:
        if upper.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


def _safe_float(value: Any) -> float | None:
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
        if len(closes) <= lookback_bd:
            perf[label] = None
            continue
        ref = _safe_float(closes.iloc[-1 - lookback_bd])
        if ref is None or ref == 0:
            perf[label] = None
            continue
        perf[label] = round((last_close - ref) / ref * 100, 4)

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
# DART 분기/연간 재무
# ---------------------------------------------------------------------------


async def _fetch_dart_quarterlies(
    stock_code: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Last 4 quarters of (fundamentals, cashflow) by differencing cumulative DART reports.

    Each quarter's single-period figure = current cumulative − previous cumulative
    (Q1 is itself single-period). Reports are fetched in parallel through
    ``dart_client.fetch_finstate_extracted`` so the whole grid (3 years × 4
    reports = up to 12 calls) hits Redis at most once per (year, reprt) and
    backs off on transient DART failures.
    """
    today = datetime.now(_KST)
    # 직전 2년 + 당해 — 연초 (예: 1~3월) 에는 직전 해 FY 미공시라 (마감 90일 전)
    # 직전-1 의 Q4 까지 거슬러 올라가야 quarters[-4:] 가 4개 채워짐.
    years = (today.year - 2, today.year - 1, today.year)

    grid_keys = [(year, label, code) for year in years for label, code in _DART_REPRT_CODES]
    extracted_list = await asyncio.gather(
        *(fetch_finstate_extracted(stock_code, year, code) for year, _label, code in grid_keys),
    )
    cumulative: dict[tuple[int, str], dict[str, float | None]] = {}
    for (year, label, _code), extracted in zip(grid_keys, extracted_list, strict=True):
        if extracted is None:
            # DART_API_KEY 미설정 — 한 번이라도 None 이면 모두 None (전체 fail).
            return [], []
        cumulative[(year, label)] = extracted

    quarters: list[dict[str, Any]] = []
    period_sequence = [(y, lbl) for y in sorted(years) for lbl, _ in _DART_REPRT_CODES]
    for year, label in period_sequence:
        cum = cumulative.get((year, label))
        if cum is None or all(cum[k] is None for k in cum):
            continue
        if label == "Q1":
            single = dict(cum)
        else:
            prev = cumulative.get((year, _DART_PREV_LABEL[label]))
            if prev is None or all(prev[k] is None for k in prev):
                continue
            single = {
                k: (cum[k] - prev[k]) if (cum[k] is not None and prev[k] is not None) else None
                for k in cum
            }
        period = f"{year} {_DART_QUARTER_LABEL[label]}"
        quarters.append({"period": period, **single})

    quarters = quarters[-4:]
    fundamentals = [
        {"period": q["period"], **{k: q[k] for k in _FUNDAMENTAL_KEYS}} for q in quarters
    ]
    cashflow = [
        {"period": q["period"], **{k: q[k] for k in _CASHFLOW_KEYS}} for q in quarters
    ]
    return fundamentals, cashflow


async def _fetch_dart_annuals(
    stock_code: str, lookback_years: int = _ANNUAL_LOOKBACK_YEARS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Annual fundamentals + cashflow for the last ``lookback_years`` years.

    Uses only the FY (사업보고서, ``reprt_code=11011``) report — its
    ``thstrm_amount`` is itself the year cumulative, so no differencing.
    Years with no FY filed yet (current year before annual release) are
    silently dropped, so callers never need to filter empty rows.
    """
    today = datetime.now(_KST)
    years = list(range(today.year - lookback_years, today.year + 1))
    extracted_list = await asyncio.gather(
        *(fetch_finstate_extracted(stock_code, year, "11011") for year in years),
    )
    fundamentals: list[dict[str, Any]] = []
    cashflow: list[dict[str, Any]] = []
    for year, extracted in zip(years, extracted_list, strict=True):
        if extracted is None:
            return [], []
        # Drop years where every value is None (typically future years with no
        # filing yet) — leaves a clean year-keyed series.
        if all(extracted[k] is None for k in extracted):
            continue
        period = str(year)
        fundamentals.append(
            {"period": period, **{k: extracted[k] for k in _FUNDAMENTAL_KEYS}}
        )
        cashflow.append(
            {"period": period, **{k: extracted[k] for k in _CASHFLOW_KEYS}}
        )
    return fundamentals, cashflow


# ---------------------------------------------------------------------------
# Source class
# ---------------------------------------------------------------------------


class KoreanFundamentalsSource:
    """quote + performance + quarterly/annual fundamentals + cashFlow for KR ticker.

    revenueByProduct / revenueByGeo / earningsSurprises / analyst consensus 는
    별도 source — XBRL 본문 파싱 (segment) / 별도 컨센서스 데이터 필요.
    """

    @staticmethod
    def supports(symbol: str) -> bool:
        return symbol.strip().upper().endswith(_KR_SUFFIXES)

    async def get_overview(self, symbol: str) -> dict[str, Any]:
        """Return CompanyOverviewResponse 호환 dict (KR 채울 수 있는 부분만).

        Quote / performance / quarterly DART / annual DART 4 paths run in
        ``asyncio.gather`` — DART subgraphs deduplicate via the Redis cache
        layer, so the annual call shares cache with the FY entries needed
        for Q4 derivation.
        """
        symbol = symbol.strip()
        ticker = _strip_suffix(symbol)
        symbol_upper = symbol.upper()

        quote, performance, quarter_data, annual_data = await asyncio.gather(
            asyncio.to_thread(_fetch_quote_from_yf, symbol_upper),
            asyncio.to_thread(_fetch_performance_from_pykrx, ticker),
            _fetch_dart_quarterlies(ticker),
            _fetch_dart_annuals(ticker),
        )
        fundamentals, cashflow = quarter_data
        annual_fundamentals, annual_cashflow = annual_data

        if not quote:
            return {
                "symbol": symbol_upper,
                "name": None,
                "quote": None,
                "performance": None,
                "_partial": True,
            }

        return {
            "symbol": symbol_upper,
            "name": None,
            "quote": quote,
            "performance": performance,
            "quarterlyFundamentals": fundamentals or None,
            "cashFlow": cashflow or None,
            "annualFundamentals": annual_fundamentals or None,
            "annualCashFlow": annual_cashflow or None,
            # 별도 issue — KR 컨센서스 source / XBRL segment 파싱 필요.
            "analystRatings": None,
            "earningsSurprises": None,
            "revenueByProduct": None,
            "revenueByGeo": None,
        }

"""KR fundamentals — quote enrichment + performance for KOSPI/KOSDAQ tickers.

FORK (#42 Stage A+B):
* Quote (시가총액 / 52W / dayHigh-Low / shares): yfinance ``fast_info`` 사용.
  pykrx 의 ``get_market_fundamental`` / ``get_market_cap`` 은 KRX 사이트 응답
  구조 변경으로 현재 KeyError 발생 — yfinance fast_info 가 KR ticker 도 안정적
  으로 처리.
* Performance (1D / 5D / 1M / 3M / 6M / 1Y / YTD %): pykrx OHLCV 로 계산.
  pykrx 의 일별 종가 endpoint 는 정상 동작.
* PER / PBR / EPS / 배당: 본 source 에서는 미채움 (yf ``info`` 느려 timeout
  위험. DART 분기 보고서 기반은 별도 issue).
* analystRatings / quarterlyFundamentals / cashFlow / revenueByProduct/Geo:
  별도 issue (DART 분기 사업보고서 파싱) 로 분리.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

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


def _strip_suffix(symbol: str) -> str:
    upper = symbol.upper()
    for suffix in _KR_SUFFIXES:
        if upper.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


def _safe_float(value: Any) -> float | None:
    """Coerce to float, returning None for NaN / None / non-numeric."""
    if value is None:
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


class KoreanFundamentalsSource:
    """quote + performance for KR ticker (.KS / .KQ).

    분기 사업보고서 기반 quarterlyFundamentals / cashFlow / revenueByProduct/Geo
    는 별도 source (DART 통합 — issue #42 Stage C) 로 분리.
    """

    @staticmethod
    def supports(symbol: str) -> bool:
        # is_unsupported_analyst_market / _is_kr_symbol 와 동일하게 strip + upper 정규화.
        return symbol.strip().upper().endswith(_KR_SUFFIXES)

    async def get_overview(self, symbol: str) -> dict[str, Any]:
        """Return CompanyOverviewResponse 호환 dict (KR 채울 수 있는 부분만).

        외부 호출 (yfinance + pykrx) 은 ``asyncio.to_thread`` 로 wrap.
        """
        # FORK (#42): supports() / is_unsupported_analyst_market 와 동일하게 strip 먼저.
        # 호출자가 이미 normalize 했더라도 source 자체가 raw input 안전 처리.
        symbol = symbol.strip()
        ticker = _strip_suffix(symbol)
        symbol_upper = symbol.upper()

        quote, performance = await asyncio.gather(
            asyncio.to_thread(_fetch_quote_from_yf, symbol_upper),
            asyncio.to_thread(_fetch_performance_from_pykrx, ticker),
        )

        # quote 가 비었으면 unsupported 와 동일 — caller 가 unsupported flag 다시 세움.
        if not quote:
            return {
                "symbol": symbol_upper,
                "name": None,
                "quote": None,
                "performance": None,
                "_partial": True,
            }

        # name 은 yfinance fast_info 에 없음. Ticker.info 는 비싸서 생략 — 별도 PR.
        return {
            "symbol": symbol_upper,
            "name": None,
            "quote": quote,
            "performance": performance,
            # 본 PR 에서 채우지 않음 — DART 통합 별도 PR.
            "analystRatings": None,
            "quarterlyFundamentals": None,
            "earningsSurprises": None,
            "cashFlow": None,
            "revenueByProduct": None,
            "revenueByGeo": None,
        }

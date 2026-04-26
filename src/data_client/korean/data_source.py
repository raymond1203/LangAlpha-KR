"""MarketDataSource implementation backed by pykrx.

Provides KOSPI/KOSDAQ daily OHLCV data and stock snapshots only.

Unsupported requests raise to trigger ``MarketDataProvider`` fallback to the
next source in the chain (silent empty results would be mistaken for success):
- Intraday intervals → ``ValueError`` (pykrx is daily-only).
- ``get_snapshots`` with ``asset_type != "stocks"`` (e.g. ``"indices"``) →
  ``NotImplementedError`` (pykrx exposes index OHLCV via a separate API not
  wired up here; index snapshots route to yfinance instead).

Symbols are expected in Yahoo-style format (e.g. ``005930.KS`` for KOSPI,
``263750.KQ`` for KOSDAQ). The ``.KS``/``.KQ`` suffix is stripped before
calling pykrx, which uses bare 6-digit tickers.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd
from pykrx import stock

logger = logging.getLogger(__name__)

_KST = ZoneInfo("Asia/Seoul")

_KR_SUFFIXES = (".KS", ".KQ")


def _strip_suffix(symbol: str) -> str:
    """Remove .KS / .KQ suffix (case-insensitive), returning the bare ticker."""
    upper = symbol.upper()
    for suffix in _KR_SUFFIXES:
        if upper.endswith(suffix):
            return symbol[: -len(suffix)]
    return symbol


def _to_int_safe(value: Any) -> int:
    """Convert to int, defaulting to 0 for NaN."""
    if pd.isna(value):
        return 0
    return int(value)


def _to_float_safe(value: Any, ndigits: int = 4) -> float:
    """Convert to rounded float, defaulting to 0.0 for NaN."""
    if pd.isna(value):
        return 0.0
    return round(float(value), ndigits)


def _normalize_bar(idx: Any, row: Any) -> dict[str, Any]:
    """Convert a pykrx OHLCV row to the standard bar shape.

    Returns ``{time, open, high, low, close, volume}`` where ``time``
    is Unix milliseconds — matching the protocol in ``base.py``.
    """
    if hasattr(idx, "timestamp"):
        t = int(idx.timestamp() * 1000)
    else:
        t = 0
    return {
        "time": t,
        "open": _to_float_safe(row["시가"]),
        "high": _to_float_safe(row["고가"]),
        "low": _to_float_safe(row["저가"]),
        "close": _to_float_safe(row["종가"]),
        "volume": _to_int_safe(row["거래량"]),
    }


def _fetch_daily(
    ticker: str,
    from_date: str | None,
    to_date: str | None,
) -> list[dict[str, Any]]:
    """Synchronous helper — called via ``asyncio.to_thread``."""
    if from_date:
        start = from_date.replace("-", "")
    else:
        start = (datetime.now(_KST) - timedelta(days=730)).strftime("%Y%m%d")

    if to_date:
        end = to_date.replace("-", "")
    else:
        end = datetime.now(_KST).strftime("%Y%m%d")

    df = stock.get_market_ohlcv(start, end, ticker)
    if df is None or df.empty:
        return []

    return [_normalize_bar(idx, row) for idx, row in df.iterrows()]


def _fetch_single_snapshot(
    ticker: str, original_symbol: str,
) -> dict[str, Any] | None:
    """Fetch latest-day snapshot for a single Korean ticker.

    Fetches a 7-day range to ensure we always have the previous trading
    day for change/change_pct calculation.
    """
    try:
        today = datetime.now(_KST).strftime("%Y%m%d")
        start = (datetime.now(_KST) - timedelta(days=7)).strftime("%Y%m%d")
        df = stock.get_market_ohlcv(start, today, ticker)

        if df is None or df.empty:
            return None

        row = df.iloc[-1]
        price = _to_float_safe(row["종가"])
        open_price = _to_float_safe(row["시가"])

        prev_close = 0.0
        if len(df) >= 2:
            prev_close = _to_float_safe(df.iloc[-2]["종가"])
        change = price - prev_close if prev_close else 0.0
        change_pct = (change / prev_close * 100) if prev_close else 0.0

        return {
            "symbol": original_symbol,
            "name": None,
            "price": round(price, 4),
            "change": round(change, 4),
            "change_percent": round(change_pct, 4),
            "previous_close": round(prev_close, 4),
            "open": round(open_price, 4),
            "high": _to_float_safe(row["고가"]),
            "low": _to_float_safe(row["저가"]),
            "volume": _to_int_safe(row["거래량"]),
            "market_status": None,
            "early_trading_change_percent": None,
            "late_trading_change_percent": None,
        }
    except Exception:
        logger.warning("korean.snapshot.failed | ticker=%s", ticker, exc_info=True)
        return None


class KoreanDataSource:
    """Market data source backed by pykrx (KOSPI/KOSDAQ)."""

    async def get_intraday(
        self,
        symbol: str,
        interval: str,
        from_date: str | None = None,
        to_date: str | None = None,
        is_index: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        raise ValueError(
            f"Interval '{interval}' is not supported by pykrx (daily only)"
        )

    async def get_daily(
        self,
        symbol: str,
        from_date: str | None = None,
        to_date: str | None = None,
        is_index: bool = False,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        ticker = _strip_suffix(symbol)
        return await asyncio.to_thread(_fetch_daily, ticker, from_date, to_date)

    async def get_snapshots(
        self,
        symbols: list[str],
        asset_type: str = "stocks",
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        # FORK: pykrx 는 KOSPI/KOSDAQ 주식 snapshot 만 지원. 인덱스(KS11/KQ11/...) 는 미지원.
        # 빈 list 반환 시 MarketDataProvider 가 "성공한 결과" 로 인식해 yfinance 로 fallback 안 됨 →
        # NotImplementedError 를 raise 해 chain 의 다음 source 로 넘기도록 함 (get_intraday 와 동일 패턴).
        if asset_type != "stocks":
            raise NotImplementedError(
                f"KoreanDataSource only supports stock snapshots, got asset_type={asset_type!r}"
            )

        if not symbols:
            return []

        results = await asyncio.gather(
            *(
                asyncio.to_thread(
                    _fetch_single_snapshot, _strip_suffix(s), s,
                )
                for s in symbols
            )
        )
        return [r for r in results if r is not None]

    async def get_market_status(
        self,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(_KST)
        hour = now.hour
        minute = now.minute
        weekday = now.weekday()

        is_weekday = weekday < 5
        is_open = is_weekday and (
            (hour == 9 and minute >= 0) or (9 < hour < 15) or (hour == 15 and minute <= 30)
        )

        return {
            "market": "open" if is_open else "closed",
            "afterHours": False,
            "earlyHours": False,
            "serverTime": now.isoformat(),
            "exchanges": None,
        }

    async def close(self) -> None:
        pass

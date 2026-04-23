"""Korean Market Price MCP Server.

Provides KOSPI/KOSDAQ stock data via pykrx — OHLCV history, market cap,
fundamentals (PER/PBR/DIV/EPS), ticker search, and full-market snapshots.

Tools:
- get_kr_stock_ohlcv: OHLCV history for a single Korean stock
- get_kr_market_cap: Market capitalisation and trading value
- get_kr_fundamental: PER, PBR, EPS, BPS, DIV for a stock
- search_kr_ticker: Look up Korean tickers by name or list by market
- get_kr_market_snapshot: Full-market OHLCV snapshot for a single date
"""

from __future__ import annotations

import time
from typing import Any, Optional

import pandas as pd
from mcp.server.fastmcp import FastMCP
from pykrx import stock


# ---------------------------------------------------------------------------
# Helpers — NaN-safe conversions
# ---------------------------------------------------------------------------


def _to_int(value: Any) -> int | None:
    """Convert to int, returning None for NaN/invalid values."""
    if pd.isna(value):
        return None
    return int(value)


def _to_float(value: Any, ndigits: int = 4) -> float | None:
    """Convert to rounded float, returning None for NaN/invalid values."""
    if pd.isna(value):
        return None
    return round(float(value), ndigits)


def _normalize_date(date_str: str) -> str:
    """Accept both YYYYMMDD and YYYY-MM-DD, return YYYYMMDD."""
    return date_str.replace("-", "")


def _row_to_ohlcv(row: Any, columns: Any, *, ticker: str | None = None) -> dict[str, Any]:
    """Convert a single pykrx OHLCV row to a normalized dict.

    Shared by _serialize_ohlcv (date-indexed) and get_kr_market_snapshot
    (ticker-indexed) to avoid duplicating column mapping and NaN handling.
    """
    record: dict[str, Any] = {}
    if ticker is not None:
        record["ticker"] = ticker
    record["open"] = _to_int(row["시가"])
    record["high"] = _to_int(row["고가"])
    record["low"] = _to_int(row["저가"])
    record["close"] = _to_int(row["종가"])
    record["volume"] = _to_int(row["거래량"])
    if "거래대금" in columns:
        record["trading_value"] = _to_int(row["거래대금"])
    if "등락률" in columns:
        record["change_pct"] = _to_float(row["등락률"], 2)
    return record


def _serialize_ohlcv(df: pd.DataFrame) -> list[dict]:
    """Convert pykrx OHLCV DataFrame to list of record dicts."""
    if df is None or df.empty:
        return []

    records = []
    for idx, row in df.iterrows():
        record = _row_to_ohlcv(row, df.columns)
        record["date"] = idx.strftime("%Y-%m-%d")
        records.append(record)

    return records


def _serialize_market_cap(df: pd.DataFrame) -> list[dict]:
    """Convert pykrx market-cap DataFrame to list of record dicts."""
    if df is None or df.empty:
        return []

    records = []
    for idx, row in df.iterrows():
        records.append({
            "date": idx.strftime("%Y-%m-%d"),
            "market_cap": _to_int(row["시가총액"]),
            "volume": _to_int(row["거래량"]),
            "trading_value": _to_int(row["거래대금"]),
            "listed_shares": _to_int(row["상장주식수"]),
        })

    return records


def _serialize_fundamental(df: pd.DataFrame) -> list[dict]:
    """Convert pykrx fundamental DataFrame to list of record dicts."""
    if df is None or df.empty:
        return []

    records = []
    for idx, row in df.iterrows():
        record: dict[str, Any] = {"date": idx.strftime("%Y-%m-%d")}
        for col in ("BPS", "PER", "PBR", "EPS", "DIV", "DPS"):
            if col in df.columns:
                record[col.lower()] = _to_float(row[col], 4)
        records.append(record)

    return records


def _make_response(
    data_type: str, data: Any, count: int | None = None, **extra: Any
) -> dict:
    resp: dict[str, Any] = {
        "data_type": data_type,
        "source": "pykrx",
        "data": data,
    }
    if count is not None:
        resp["count"] = count
    elif isinstance(data, list):
        resp["count"] = len(data)
    resp.update(extra)
    return resp


def _make_error(msg: str) -> dict:
    return {"error": msg}


# ---------------------------------------------------------------------------
# Ticker name cache — avoids per-ticker HTTP calls in search_kr_ticker
# ---------------------------------------------------------------------------

_ticker_cache: dict[str, list[dict[str, str]]] = {}
_ticker_cache_ts: dict[str, float] = {}
_TICKER_CACHE_TTL = 3600  # 1 hour


def _get_ticker_list(market: str, ref_date: str | None) -> list[dict[str, str]]:
    """Return [{ticker, name}, ...] for a market, using a TTL cache."""
    cache_key = f"{market}:{ref_date or 'latest'}"
    now = time.monotonic()

    if cache_key in _ticker_cache and (now - _ticker_cache_ts[cache_key]) < _TICKER_CACHE_TTL:
        return _ticker_cache[cache_key]

    if ref_date:
        tickers = stock.get_market_ticker_list(ref_date, market=market)
    else:
        tickers = stock.get_market_ticker_list(market=market)

    entries = [
        {"ticker": t, "name": stock.get_market_ticker_name(t)}
        for t in tickers
    ]

    _ticker_cache[cache_key] = entries
    _ticker_cache_ts[cache_key] = now
    return entries


# ---------------------------------------------------------------------------
# MCP server + tools
# ---------------------------------------------------------------------------

mcp = FastMCP("KoreanPriceMCP")


@mcp.tool()
def get_kr_stock_ohlcv(
    ticker: str,
    from_date: str,
    to_date: str,
    adjusted: bool = True,
    freq: str = "d",
) -> dict:
    """Get historical OHLCV price data for a Korean stock (KOSPI/KOSDAQ).

    Returns bars with open, high, low, close, volume, trading_value,
    and change_pct. Prices are in KRW. Dates in YYYY-MM-DD format.

    Args:
        ticker: 6-digit Korean stock ticker (e.g. "005930" for Samsung Electronics)
        from_date: Start date — YYYYMMDD or YYYY-MM-DD
        to_date: End date — YYYYMMDD or YYYY-MM-DD
        adjusted: Whether to use adjusted prices (default True)
        freq: Frequency — "d" (daily), "m" (monthly), "y" (yearly)

    Returns:
        dict: OHLCV records with date, open, high, low, close, volume,
        trading_value, change_pct.
    """
    try:
        start = _normalize_date(from_date)
        end = _normalize_date(to_date)

        df = stock.get_market_ohlcv(start, end, ticker, adjusted=adjusted, freq=freq)
        records = _serialize_ohlcv(df)

        return _make_response(
            "kr_stock_ohlcv",
            records,
            ticker=ticker,
            from_date=from_date,
            to_date=to_date,
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"Failed to fetch OHLCV for {ticker}: {e}")


@mcp.tool()
def get_kr_market_cap(
    ticker: str,
    from_date: str,
    to_date: str,
    freq: str = "d",
) -> dict:
    """Get market capitalisation, trading value, and listed shares for a Korean stock.

    Args:
        ticker: 6-digit Korean stock ticker (e.g. "005930")
        from_date: Start date — YYYYMMDD or YYYY-MM-DD
        to_date: End date — YYYYMMDD or YYYY-MM-DD
        freq: Frequency — "d" (daily), "m" (monthly), "y" (yearly)

    Returns:
        dict: Records with date, market_cap (KRW), volume, trading_value,
        listed_shares.
    """
    try:
        start = _normalize_date(from_date)
        end = _normalize_date(to_date)

        df = stock.get_market_cap(start, end, ticker, freq=freq)
        records = _serialize_market_cap(df)

        return _make_response(
            "kr_market_cap",
            records,
            ticker=ticker,
            from_date=from_date,
            to_date=to_date,
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"Failed to fetch market cap for {ticker}: {e}")


@mcp.tool()
def get_kr_fundamental(
    ticker: str,
    from_date: str,
    to_date: str,
    freq: str = "d",
) -> dict:
    """Get fundamental valuation data (PER, PBR, EPS, BPS, DIV, DPS) for a Korean stock.

    Args:
        ticker: 6-digit Korean stock ticker (e.g. "005930")
        from_date: Start date — YYYYMMDD or YYYY-MM-DD
        to_date: End date — YYYYMMDD or YYYY-MM-DD
        freq: Frequency — "d" (daily), "m" (monthly), "y" (yearly)

    Returns:
        dict: Records with date, bps, per, pbr, eps, div, dps.
    """
    try:
        start = _normalize_date(from_date)
        end = _normalize_date(to_date)

        df = stock.get_market_fundamental(start, end, ticker, freq=freq)
        records = _serialize_fundamental(df)

        return _make_response(
            "kr_fundamental",
            records,
            ticker=ticker,
            from_date=from_date,
            to_date=to_date,
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"Failed to fetch fundamentals for {ticker}: {e}")


@mcp.tool()
def search_kr_ticker(
    query: str,
    market: str = "ALL",
    date: Optional[str] = None,
    limit: int = 50,
) -> dict:
    """Search for Korean stock tickers by company name, or list all tickers in a market.

    If query is provided, filters tickers whose name contains the query string.
    If query is empty or "*", returns all tickers for the given market.

    Results are cached for 1 hour to avoid repeated HTTP calls to KRX.

    Args:
        query: Company name to search (e.g. "삼성", "카카오"). Use "*" for all.
        market: Market filter — "KOSPI", "KOSDAQ", "KONEX", or "ALL" (default)
        date: Reference date for ticker list — YYYYMMDD or YYYY-MM-DD (default: latest)
        limit: Maximum number of results to return (default 50)

    Returns:
        dict: List of {ticker, name} objects matching the query.
    """
    try:
        ref_date = _normalize_date(date) if date else None
        all_tickers = _get_ticker_list(market, ref_date)

        if query and query != "*":
            results = [t for t in all_tickers if query in t["name"]]
        else:
            results = list(all_tickers)

        results = results[:limit]

        return _make_response(
            "kr_ticker_search",
            results,
            query=query,
            market=market,
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"Failed to search tickers for '{query}': {e}")


@mcp.tool()
def get_kr_market_snapshot(
    date: str,
    market: str = "KOSPI",
) -> dict:
    """Get OHLCV snapshot for all stocks in a Korean market on a single date.

    Useful for screening, ranking, or building a market heatmap.

    Args:
        date: Target date — YYYYMMDD or YYYY-MM-DD
        market: Market — "KOSPI", "KOSDAQ", "KONEX", or "ALL"

    Returns:
        dict: List of {ticker, open, high, low, close, volume, trading_value,
        change_pct} for every stock in the market.
    """
    try:
        ref_date = _normalize_date(date)

        df = stock.get_market_ohlcv(ref_date, market=market)

        if df is None or df.empty:
            return _make_response("kr_market_snapshot", [], date=date, market=market)

        records = []
        for ticker_code, row in df.iterrows():
            record = _row_to_ohlcv(row, df.columns, ticker=str(ticker_code))
            records.append(record)

        return _make_response(
            "kr_market_snapshot",
            records,
            date=date,
            market=market,
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"Failed to fetch market snapshot for {date}: {e}")


if __name__ == "__main__":
    mcp.run(transport="stdio")

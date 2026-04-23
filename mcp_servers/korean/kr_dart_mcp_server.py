"""Korean DART Disclosure MCP Server.

Provides DART (전자공시시스템) data via OpenDartReader — financial statements,
disclosure lists, company search, and major shareholder reports.

Requires DART_API_KEY environment variable.
Get a free key at https://opendart.fss.or.kr/

Tools:
- search_dart_corp: Find corp_code by company name or stock code
- get_dart_company_info: Company profile from DART
- get_dart_financials: K-IFRS financial statements (BS, IS, CF)
- get_dart_financials_all: Full financial statements (all XBRL items)
- get_dart_disclosures: Disclosure list with type filters
- get_dart_major_shareholders: Major shareholder reports (5% rule)
"""

from __future__ import annotations

import os
from typing import Any, Optional

import pandas as pd
from mcp.server.fastmcp import FastMCP


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_dart():
    """Initialize OpenDartReader with API key from environment."""
    import OpenDartReader

    api_key = os.getenv("DART_API_KEY")
    if not api_key:
        msg = (
            "DART_API_KEY 환경변수가 설정되지 않았습니다. "
            "https://opendart.fss.or.kr/ 에서 무료 발급 후 .env에 추가하세요."
        )
        raise RuntimeError(msg)
    return OpenDartReader(api_key)


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert DataFrame to list of dicts with NaN-safe serialization."""
    if df is None or (isinstance(df, pd.DataFrame) and df.empty):
        return []

    records = []
    for _, row in df.iterrows():
        record = {}
        for col in df.columns:
            val = row[col]
            if pd.isna(val):
                record[col] = None
            elif isinstance(val, float):
                record[col] = round(val, 4)
            else:
                record[col] = val
        records.append(record)
    return records


def _make_response(
    data_type: str, data: Any, count: int | None = None, **extra: Any,
) -> dict:
    resp: dict[str, Any] = {
        "data_type": data_type,
        "source": "dart",
        "data": data,
    }
    if count is not None:
        resp["count"] = count
    elif isinstance(data, list):
        resp["count"] = len(data)
    elif isinstance(data, dict) and data:
        resp["count"] = 1
    resp.update(extra)
    return resp


def _make_error(msg: str) -> dict:
    return {"error": msg}


# ---------------------------------------------------------------------------
# MCP server + tools
# ---------------------------------------------------------------------------

mcp = FastMCP("KoreanDartMCP")


@mcp.tool()
def search_dart_corp(
    query: str,
) -> dict:
    """Search for a Korean company in DART by name or stock code.

    Returns company profile(s) including corp_code (DART unique ID),
    corp_name, stock_code, and other details.

    Args:
        query: Company name (e.g. "삼성전자") or 6-digit stock code (e.g. "005930")

    Returns:
        dict: List of matching companies with corp_code, corp_name, stock_code.
    """
    try:
        dart = _get_dart()

        if query.isdigit() and len(query) == 6:
            info = dart.company(query)
            if info:
                data = [info] if isinstance(info, dict) else _df_to_records(info)
                return _make_response("dart_company_search", data, query=query)
            return _make_response("dart_company_search", [], query=query)

        results = dart.company_by_name(query)
        if results is None:
            return _make_response("dart_company_search", [], query=query)
        if isinstance(results, pd.DataFrame):
            data = _df_to_records(results)
        elif isinstance(results, list):
            data = results
        else:
            data = [results] if isinstance(results, dict) else []

        return _make_response("dart_company_search", data, query=query)
    except Exception as e:  # noqa: BLE001
        return _make_error(f"DART 기업 검색 실패 '{query}': {e}")


@mcp.tool()
def get_dart_company_info(
    corp: str,
) -> dict:
    """Get company profile information from DART.

    Args:
        corp: Company name, stock code (e.g. "005930"), or DART corp_code

    Returns:
        dict: Company profile with corp_code, corp_name, ceo_nm, corp_cls,
        adres, hm_url, ir_url, est_dt, etc.
    """
    try:
        dart = _get_dart()
        info = dart.company(corp)

        if info is None:
            return _make_response("dart_company_info", {}, corp=corp)
        if isinstance(info, dict):
            data = info
        elif isinstance(info, pd.DataFrame):
            data = _df_to_records(info)
        else:
            data = {}

        return _make_response("dart_company_info", data, corp=corp)
    except Exception as e:  # noqa: BLE001
        return _make_error(f"DART 기업 정보 조회 실패 '{corp}': {e}")


@mcp.tool()
def get_dart_financials(
    corp: str,
    year: int,
    reprt_code: str = "11011",
) -> dict:
    """Get K-IFRS financial statements from DART.

    Returns key items from balance sheet, income statement, and cash flow.
    Can query single or multiple companies at once.

    Args:
        corp: Company name, stock code, or corp_code.
              For multiple companies, comma-separate: "005930, 000660"
        year: Business year (e.g. 2024)
        reprt_code: Report type — "11011" (annual), "11013" (Q1),
                    "11012" (half), "11014" (Q3)

    Returns:
        dict: Financial statement items with account_nm, thstrm_amount,
        frmtrm_amount, bfefrmtrm_amount, etc.
    """
    try:
        dart = _get_dart()
        result = dart.finstate(corp, year, reprt_code=reprt_code)

        if result is None or (isinstance(result, pd.DataFrame) and result.empty):
            return _make_response(
                "dart_financials", [], corp=corp, year=year, reprt_code=reprt_code,
            )

        records = _df_to_records(result)
        return _make_response(
            "dart_financials", records, corp=corp, year=year, reprt_code=reprt_code,
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"DART 재무제표 조회 실패 '{corp}' {year}: {e}")


@mcp.tool()
def get_dart_financials_all(
    corp: str,
    year: int,
    reprt_code: str = "11011",
) -> dict:
    """Get full financial statements from DART (all XBRL items).

    Unlike get_dart_financials which returns key items only, this returns
    every line item from the XBRL filing — useful for detailed analysis.

    Args:
        corp: Stock code (e.g. "005930") or corp_code
        year: Business year (e.g. 2024)
        reprt_code: Report type — "11011" (annual), "11013" (Q1),
                    "11012" (half), "11014" (Q3)

    Returns:
        dict: All financial statement line items.
    """
    try:
        dart = _get_dart()
        result = dart.finstate_all(corp, year, reprt_code=reprt_code)

        if result is None or (isinstance(result, pd.DataFrame) and result.empty):
            return _make_response(
                "dart_financials_all", [], corp=corp, year=year, reprt_code=reprt_code,
            )

        records = _df_to_records(result)
        return _make_response(
            "dart_financials_all", records, corp=corp, year=year, reprt_code=reprt_code,
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"DART 전체 재무제표 조회 실패 '{corp}' {year}: {e}")


@mcp.tool()
def get_dart_disclosures(
    corp: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    kind: str = "",
) -> dict:
    """Get disclosure list from DART for a company.

    Args:
        corp: Company name, stock code, or corp_code
        start: Start date — YYYY-MM-DD (default: 1999-01-01)
        end: End date — YYYY-MM-DD (default: today)
        kind: Disclosure type filter:
              "" (all), "A" (정기공시), "B" (주요사항공시),
              "C" (발행공시), "D" (지분공시), "E" (기타공시)

    Returns:
        dict: List of disclosures with rcept_no, corp_name, report_nm,
        rcept_dt, etc.
    """
    try:
        dart = _get_dart()

        kwargs: dict[str, Any] = {}
        if start:
            kwargs["start"] = start
        if end:
            kwargs["end"] = end
        if kind:
            kwargs["kind"] = kind

        result = dart.list(corp, **kwargs)

        if result is None or (isinstance(result, pd.DataFrame) and result.empty):
            return _make_response(
                "dart_disclosures", [], corp=corp, kind=kind or "all",
            )

        records = _df_to_records(result)
        return _make_response(
            "dart_disclosures", records, corp=corp, kind=kind or "all",
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"DART 공시 목록 조회 실패 '{corp}': {e}")


@mcp.tool()
def get_dart_major_shareholders(
    corp: str,
) -> dict:
    """Get major shareholder reports (5% rule) from DART.

    Returns large shareholding status reports for a company.

    Args:
        corp: Company name, stock code (e.g. "005930"), or corp_code

    Returns:
        dict: Major shareholder reports with shareholder details,
        share counts, and ownership percentages.
    """
    try:
        dart = _get_dart()
        result = dart.major_shareholders(corp)

        if result is None or (isinstance(result, pd.DataFrame) and result.empty):
            return _make_response("dart_major_shareholders", [], corp=corp)

        records = _df_to_records(result)
        return _make_response("dart_major_shareholders", records, corp=corp)
    except Exception as e:  # noqa: BLE001
        return _make_error(f"DART 대량보유 조회 실패 '{corp}': {e}")


if __name__ == "__main__":
    mcp.run(transport="stdio")

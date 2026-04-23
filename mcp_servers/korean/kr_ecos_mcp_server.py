"""Korean ECOS Macro MCP Server.

Provides Korean macroeconomic data via the Bank of Korea ECOS API —
base rate, GDP, CPI, exchange rates, and treasury yields.

Requires ECOS_API_KEY environment variable.
Get a free key at https://ecos.bok.or.kr/api/

Tools:
- get_kr_base_rate: Bank of Korea base interest rate
- get_kr_economic_indicator: GDP, CPI, unemployment, and other indicators
- get_kr_exchange_rate: KRW exchange rates (USD, JPY, EUR, etc.)
- get_kr_treasury_yield: Korean government bond yields (3Y, 5Y, 10Y, etc.)
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx
from mcp.server.fastmcp import FastMCP


# ---------------------------------------------------------------------------
# ECOS API client
# ---------------------------------------------------------------------------

_ECOS_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"
_HTTP_TIMEOUT = 30.0

# Indicator presets: name -> (stat_code, item_code, cycle)
_INDICATOR_PRESETS: dict[str, tuple[str, str, str]] = {
    "gdp_growth": ("200Y002", "10111", "Q"),
    "cpi": ("021Y125", "0", "M"),
    "unemployment": ("901Y027", "3130000", "M"),
    "export": ("403Y003", "10000", "M"),
    "import": ("403Y003", "20000", "M"),
    "industrial_production": ("901Y033", "I11B", "M"),
    "consumer_sentiment": ("511Y002", "FME", "M"),
    "money_supply_m2": ("101Y003", "BBGA00", "M"),
}

# Exchange rate item codes (under stat_code 731Y001)
_EXCHANGE_RATE_CODES: dict[str, str] = {
    "USD": "0000001",
    "JPY": "0000002",
    "EUR": "0000003",
    "GBP": "0000004",
    "CAD": "0000009",
    "CHF": "0000006",
    "AUD": "0000013",
    "CNY": "0000053",
}

# Treasury yield item codes (under stat_code 817Y002)
_TREASURY_CODES: dict[str, str] = {
    "1Y": "010190000",
    "2Y": "010195000",
    "3Y": "010200000",
    "5Y": "010200001",
    "10Y": "010210000",
    "20Y": "010220000",
    "30Y": "010230000",
    "50Y": "010240000",
}


def _get_api_key() -> str:
    api_key = os.getenv("ECOS_API_KEY")
    if not api_key:
        msg = (
            "ECOS_API_KEY 환경변수가 설정되지 않았습니다. "
            "https://ecos.bok.or.kr/api/ 에서 무료 발급 후 .env에 추가하세요."
        )
        raise RuntimeError(msg)
    return api_key


def _sanitize_error(error: Exception) -> str:
    """Remove API key from error messages to prevent leakage."""
    msg = str(error)
    api_key = os.getenv("ECOS_API_KEY", "")
    if api_key and api_key in msg:
        msg = msg.replace(api_key, "***")
    return msg


def _fetch_ecos(
    stat_code: str,
    item_code: str,
    cycle: str,
    start_date: str,
    end_date: str,
    start_idx: int = 1,
    end_idx: int = 500,
) -> list[dict[str, Any]]:
    """Call ECOS StatisticSearch API and return row data."""
    api_key = _get_api_key()
    start = start_date.replace("-", "")
    end = end_date.replace("-", "")

    url = (
        f"{_ECOS_BASE}/{api_key}/json/kr"
        f"/{start_idx}/{end_idx}"
        f"/{stat_code}/{cycle}/{start}/{end}/{item_code}"
    )

    try:
        with httpx.Client(timeout=_HTTP_TIMEOUT) as client:
            resp = client.get(url)
            resp.raise_for_status()
            data = resp.json()
    except (httpx.HTTPStatusError, httpx.RequestError) as exc:
        raise RuntimeError(_sanitize_error(exc)) from exc

    if "RESULT" in data:
        code = data["RESULT"].get("CODE", "")
        message = data["RESULT"].get("MESSAGE", "Unknown error")
        if code.startswith("ERROR"):
            msg = f"ECOS API 오류 ({code}): {message}"
            raise RuntimeError(msg)

    stat = data.get("StatisticSearch")
    if not stat or "row" not in stat:
        return []

    return stat["row"]


def _format_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize ECOS row data to a consistent format."""
    records = []
    for row in rows:
        record: dict[str, Any] = {
            "date": row.get("TIME", ""),
            "value": row.get("DATA_VALUE", ""),
            "stat_name": row.get("STAT_NAME", ""),
            "item_name": row.get("ITEM_NAME1", ""),
            "unit": row.get("UNIT_NAME", ""),
        }
        try:
            record["value"] = float(record["value"])
        except (ValueError, TypeError):
            pass
        records.append(record)
    return records


def _make_response(
    data_type: str, data: Any, count: int | None = None, **extra: Any,
) -> dict:
    resp: dict[str, Any] = {
        "data_type": data_type,
        "source": "ecos",
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


def _require_dates(from_date: str, to_date: str) -> str | None:
    """Return error message if dates are missing, else None."""
    if not from_date or not to_date:
        return "from_date와 to_date는 필수입니다."
    return None


# ---------------------------------------------------------------------------
# MCP server + tools
# ---------------------------------------------------------------------------

mcp = FastMCP("KoreanEcosMCP")


@mcp.tool()
def get_kr_base_rate(
    from_date: str,
    to_date: str,
    cycle: str = "M",
) -> dict:
    """Get Bank of Korea base interest rate (한은 기준금리).

    Essential for WACC calculation in Korean DCF models.

    Args:
        from_date: Start date — YYYYMMDD or YYYY-MM-DD
        to_date: End date — YYYYMMDD or YYYY-MM-DD
        cycle: Data frequency — "D" (daily), "M" (monthly), "A" (annual)

    Returns:
        dict: Time series of base rate values with date, value, unit.
    """
    try:
        rows = _fetch_ecos("722Y001", "0101000", cycle, from_date, to_date)
        records = _format_rows(rows)
        return _make_response(
            "kr_base_rate", records, from_date=from_date, to_date=to_date,
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"한은 기준금리 조회 실패: {_sanitize_error(e)}")


@mcp.tool()
def get_kr_economic_indicator(
    indicator: str,
    from_date: str,
    to_date: str,
    cycle: Optional[str] = None,
) -> dict:
    """Get Korean economic indicator time series from ECOS.

    Args:
        indicator: Indicator name — one of:
            "gdp_growth" (GDP 성장률, quarterly),
            "cpi" (소비자물가지수, monthly),
            "unemployment" (실업률, monthly),
            "export" (수출, monthly),
            "import" (수입, monthly),
            "industrial_production" (산업생산지수, monthly),
            "consumer_sentiment" (소비자심리지수, monthly),
            "money_supply_m2" (M2 통화량, monthly)
        from_date: Start date — YYYYMMDD or YYYY-MM-DD
        to_date: End date — YYYYMMDD or YYYY-MM-DD
        cycle: Override frequency — "M" (monthly), "Q" (quarterly), "A" (annual).
               If not set, uses the indicator's default.

    Returns:
        dict: Time series with date, value, stat_name, item_name, unit.
    """
    try:
        preset = _INDICATOR_PRESETS.get(indicator)
        if not preset:
            available = ", ".join(sorted(_INDICATOR_PRESETS.keys()))
            return _make_error(
                f"알 수 없는 지표 '{indicator}'. 사용 가능: {available}"
            )

        stat_code, item_code, default_cycle = preset
        use_cycle = cycle or default_cycle

        rows = _fetch_ecos(stat_code, item_code, use_cycle, from_date, to_date)
        records = _format_rows(rows)
        return _make_response(
            "kr_economic_indicator", records,
            indicator=indicator, from_date=from_date, to_date=to_date,
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"경제지표 조회 실패 '{indicator}': {_sanitize_error(e)}")


@mcp.tool()
def get_kr_exchange_rate(
    currency: str = "USD",
    from_date: str = "",
    to_date: str = "",
    cycle: str = "M",
) -> dict:
    """Get KRW exchange rate (원화 환율).

    Args:
        currency: Target currency — "USD", "JPY" (100엔), "EUR", "GBP",
                  "CAD", "CHF", "AUD", "CNY"
        from_date: Start date — YYYYMMDD or YYYY-MM-DD (required)
        to_date: End date — YYYYMMDD or YYYY-MM-DD (required)
        cycle: Frequency — "D" (daily), "M" (monthly), "A" (annual)

    Returns:
        dict: Exchange rate time series (KRW per unit of foreign currency).
    """
    try:
        date_err = _require_dates(from_date, to_date)
        if date_err:
            return _make_error(date_err)

        item_code = _EXCHANGE_RATE_CODES.get(currency.upper())
        if not item_code:
            available = ", ".join(sorted(_EXCHANGE_RATE_CODES.keys()))
            return _make_error(
                f"알 수 없는 통화 '{currency}'. 사용 가능: {available}"
            )

        rows = _fetch_ecos("731Y001", item_code, cycle, from_date, to_date)
        records = _format_rows(rows)
        return _make_response(
            "kr_exchange_rate", records,
            currency=currency.upper(), from_date=from_date, to_date=to_date,
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"환율 조회 실패 '{currency}': {_sanitize_error(e)}")


@mcp.tool()
def get_kr_treasury_yield(
    maturity: str = "10Y",
    from_date: str = "",
    to_date: str = "",
    cycle: str = "M",
) -> dict:
    """Get Korean government bond yield (국고채 수익률).

    Essential for risk-free rate in Korean CAPM/DCF models.

    Args:
        maturity: Bond maturity — "1Y", "2Y", "3Y", "5Y", "10Y",
                  "20Y", "30Y", "50Y"
        from_date: Start date — YYYYMMDD or YYYY-MM-DD (required)
        to_date: End date — YYYYMMDD or YYYY-MM-DD (required)
        cycle: Frequency — "D" (daily), "M" (monthly), "A" (annual)

    Returns:
        dict: Treasury yield time series (%).
    """
    try:
        date_err = _require_dates(from_date, to_date)
        if date_err:
            return _make_error(date_err)

        item_code = _TREASURY_CODES.get(maturity.upper())
        if not item_code:
            available = ", ".join(sorted(_TREASURY_CODES.keys()))
            return _make_error(
                f"알 수 없는 만기 '{maturity}'. 사용 가능: {available}"
            )

        rows = _fetch_ecos("817Y002", item_code, cycle, from_date, to_date)
        records = _format_rows(rows)
        return _make_response(
            "kr_treasury_yield", records,
            maturity=maturity.upper(), from_date=from_date, to_date=to_date,
        )
    except Exception as e:  # noqa: BLE001
        return _make_error(f"국고채 수익률 조회 실패 '{maturity}': {_sanitize_error(e)}")


if __name__ == "__main__":
    mcp.run(transport="stdio")

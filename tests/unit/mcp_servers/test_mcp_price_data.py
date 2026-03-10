"""Tests for price_data_mcp_server: get_stock_data, get_asset_data, get_short_data."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

_MOD = "mcp_servers.price_data_mcp_server"

# ---------------------------------------------------------------------------
# Canned data
# ---------------------------------------------------------------------------

_OHLCV_ROWS = [
    {"date": "2025-01-02", "open": 100, "high": 105, "low": 99, "close": 103, "volume": 1000},
    {"date": "2025-01-03", "open": 103, "high": 108, "low": 102, "close": 107, "volume": 1200},
]

_SHORT_INTEREST = {
    "results": [
        {"ticker": "AAPL", "settlement_date": "2025-03-14", "short_interest": 133_000_000,
         "avg_daily_volume": 59_000_000, "days_to_cover": 2.25},
    ],
    "status": "OK",
}

_SHORT_VOLUME = {
    "results": [
        {"ticker": "AAPL", "date": "2025-03-25", "short_volume": 181_219,
         "total_volume": 574_084, "short_volume_ratio": 31.57},
    ],
    "status": "OK",
}


def _make_fmp_client(**overrides) -> MagicMock:
    client = AsyncMock()
    client.get_stock_price = AsyncMock(return_value=overrides.get("stock_price", _OHLCV_ROWS))
    client.get_intraday_chart = AsyncMock(return_value=overrides.get("intraday", _OHLCV_ROWS))
    client.get_commodity_price = AsyncMock(return_value=overrides.get("commodity", _OHLCV_ROWS))
    client.get_crypto_price = AsyncMock(return_value=overrides.get("crypto", _OHLCV_ROWS))
    client.get_forex_price = AsyncMock(return_value=overrides.get("forex", _OHLCV_ROWS))
    client.get_commodity_intraday_chart = AsyncMock(return_value=overrides.get("commodity_intra", _OHLCV_ROWS))
    client.get_crypto_intraday_chart = AsyncMock(return_value=overrides.get("crypto_intra", _OHLCV_ROWS))
    client.get_forex_intraday_chart = AsyncMock(return_value=overrides.get("forex_intra", _OHLCV_ROWS))
    return client


# ---------------------------------------------------------------------------
# get_stock_data
# ---------------------------------------------------------------------------

class TestGetStockData:
    @pytest.mark.asyncio
    async def test_daily(self):
        from mcp_servers.price_data_mcp_server import get_stock_data

        client = _make_fmp_client()
        with patch(f"{_MOD}.get_fmp_client", return_value=client):
            result = await get_stock_data("AAPL", interval="1day")

        assert result["symbol"] == "AAPL"
        assert result["interval"] == "1day"
        assert result["count"] == 2
        assert result["source"] == "fmp"
        # Rows should be descending
        assert result["rows"][0]["date"] >= result["rows"][1]["date"]
        client.get_stock_price.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_intraday(self):
        from mcp_servers.price_data_mcp_server import get_stock_data

        client = _make_fmp_client()
        with patch(f"{_MOD}.get_fmp_client", return_value=client):
            result = await get_stock_data(
                "AAPL", interval="5min",
                start_date="2025-01-01", end_date="2025-01-07",
            )

        assert result["interval"] == "5min"
        client.get_intraday_chart.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_intraday_missing_dates(self):
        from mcp_servers.price_data_mcp_server import get_stock_data

        result = await get_stock_data("AAPL", interval="5min")
        assert "error" in result
        assert "required" in result["error"]

    @pytest.mark.asyncio
    async def test_unsupported_interval(self):
        from mcp_servers.price_data_mcp_server import get_stock_data

        client = _make_fmp_client()
        with patch(f"{_MOD}.get_fmp_client", return_value=client):
            result = await get_stock_data("AAPL", interval="2min")

        assert "error" in result
        assert "supported" in result

    @pytest.mark.asyncio
    async def test_fmp_init_error(self):
        from mcp_servers.price_data_mcp_server import get_stock_data

        with patch(f"{_MOD}.get_fmp_client", side_effect=RuntimeError("no key")):
            result = await get_stock_data("AAPL")

        assert "error" in result
        assert "FMP" in result["error"]

    @pytest.mark.asyncio
    async def test_api_error(self):
        from mcp_servers.price_data_mcp_server import get_stock_data

        client = _make_fmp_client()
        client.get_stock_price = AsyncMock(side_effect=Exception("timeout"))
        with patch(f"{_MOD}.get_fmp_client", return_value=client):
            result = await get_stock_data("AAPL")

        assert "error" in result

    @pytest.mark.asyncio
    async def test_empty_rows(self):
        from mcp_servers.price_data_mcp_server import get_stock_data

        client = _make_fmp_client(stock_price=[])
        with patch(f"{_MOD}.get_fmp_client", return_value=client):
            result = await get_stock_data("AAPL")

        assert result["count"] == 0
        assert result["rows"] == []

    @pytest.mark.asyncio
    async def test_ohlcv_normalization(self):
        from mcp_servers.price_data_mcp_server import get_stock_data

        raw = [{"date": "2025-01-01", "open": "10", "high": None, "low": 9, "close": 10, "volume": 100}]
        client = _make_fmp_client(stock_price=raw)
        with patch(f"{_MOD}.get_fmp_client", return_value=client):
            result = await get_stock_data("AAPL")

        row = result["rows"][0]
        assert row["open"] == 10.0  # string → float
        assert row["high"] is None  # None preserved
        assert row["date"] == "2025-01-01"


# ---------------------------------------------------------------------------
# get_asset_data
# ---------------------------------------------------------------------------

class TestGetAssetData:
    @pytest.mark.asyncio
    async def test_commodity_daily(self):
        from mcp_servers.price_data_mcp_server import get_asset_data

        client = _make_fmp_client()
        with patch(f"{_MOD}.get_fmp_client", return_value=client):
            result = await get_asset_data("GCUSD", asset_type="commodity")

        assert result["asset_type"] == "commodity"
        assert result["count"] == 2
        client.get_commodity_price.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_crypto_intraday(self):
        from mcp_servers.price_data_mcp_server import get_asset_data

        client = _make_fmp_client()
        with patch(f"{_MOD}.get_fmp_client", return_value=client):
            result = await get_asset_data(
                "BTCUSD", asset_type="crypto", interval="5min",
                from_date="2025-01-01", to_date="2025-01-07",
            )

        assert result["interval"] == "5min"
        client.get_crypto_intraday_chart.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_forex_daily(self):
        from mcp_servers.price_data_mcp_server import get_asset_data

        client = _make_fmp_client()
        with patch(f"{_MOD}.get_fmp_client", return_value=client):
            result = await get_asset_data("EURUSD", asset_type="forex")

        client.get_forex_price.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invalid_asset_type(self):
        from mcp_servers.price_data_mcp_server import get_asset_data

        client = _make_fmp_client()
        with patch(f"{_MOD}.get_fmp_client", return_value=client):
            result = await get_asset_data("X", asset_type="bond")

        assert "error" in result
        assert "supported" in result

    @pytest.mark.asyncio
    async def test_unsupported_intraday_for_commodity(self):
        from mcp_servers.price_data_mcp_server import get_asset_data

        client = _make_fmp_client()
        with patch(f"{_MOD}.get_fmp_client", return_value=client):
            result = await get_asset_data("GCUSD", asset_type="commodity", interval="30min")

        assert "error" in result


# ---------------------------------------------------------------------------
# get_short_data
# ---------------------------------------------------------------------------

_GINLIX_MOD = "data_client.ginlix_data.mcp_client"


def _mock_ginlix_request(*responses):
    """Create a mock for GinlixMCPClient.request that returns canned responses."""
    mock = AsyncMock(side_effect=responses)
    return mock


def _make_response(json_data):
    resp = MagicMock()
    resp.json.return_value = json_data
    resp.raise_for_status = MagicMock()
    return resp


class TestGetShortData:
    @pytest.mark.asyncio
    async def test_no_ginlix_client(self):
        """When ginlix-data is not configured, should return an error."""
        import mcp_servers.price_data_mcp_server as mod

        with patch.object(mod._ginlix, "ensure", return_value=False), \
             patch.object(mod._ginlix, "request", new_callable=AsyncMock):
            result = await mod.get_short_data("AAPL")
            assert "error" in result
            assert "ginlix-data" in result["error"]

    @pytest.mark.asyncio
    async def test_both_data_types(self):
        """Default data_type='both' should fetch SI and SV."""
        import mcp_servers.price_data_mcp_server as mod

        si_resp = _make_response(_SHORT_INTEREST)
        sv_resp = _make_response(_SHORT_VOLUME)

        with patch.object(mod._ginlix, "ensure", return_value=True), \
             patch.object(mod._ginlix, "request", new_callable=AsyncMock,
                          side_effect=[si_resp, sv_resp]):
            result = await mod.get_short_data("AAPL")

        assert result["symbol"] == "AAPL"
        assert result["source"] == "ginlix-data"
        assert len(result["short_interest"]) == 1
        assert result["short_interest"][0]["short_interest"] == 133_000_000
        assert len(result["short_volume"]) == 1
        assert result["short_volume"][0]["short_volume_ratio"] == 31.57

    @pytest.mark.asyncio
    async def test_short_interest_only(self):
        import mcp_servers.price_data_mcp_server as mod

        si_resp = _make_response(_SHORT_INTEREST)

        with patch.object(mod._ginlix, "ensure", return_value=True), \
             patch.object(mod._ginlix, "request", new_callable=AsyncMock,
                          return_value=si_resp) as mock_req:
            result = await mod.get_short_data("AAPL", data_type="short_interest")

        assert "short_interest" in result
        assert "short_volume" not in result
        call_args = mock_req.call_args
        assert "short-interest" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_short_volume_only(self):
        import mcp_servers.price_data_mcp_server as mod

        sv_resp = _make_response(_SHORT_VOLUME)

        with patch.object(mod._ginlix, "ensure", return_value=True), \
             patch.object(mod._ginlix, "request", new_callable=AsyncMock,
                          return_value=sv_resp):
            result = await mod.get_short_data("AAPL", data_type="short_volume")

        assert "short_volume" in result
        assert "short_interest" not in result

    @pytest.mark.asyncio
    async def test_date_filters(self):
        """from_date/to_date should be passed as query params."""
        import mcp_servers.price_data_mcp_server as mod

        si_resp = _make_response(_SHORT_INTEREST)

        with patch.object(mod._ginlix, "ensure", return_value=True), \
             patch.object(mod._ginlix, "request", new_callable=AsyncMock,
                          return_value=si_resp) as mock_req:
            await mod.get_short_data(
                "AAPL", data_type="short_interest",
                from_date="2025-01-01", to_date="2025-03-31",
            )

        call_kwargs = mock_req.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["settlement_date.gte"] == "2025-01-01"
        assert params["settlement_date.lte"] == "2025-03-31"
        assert params["sort"] == "settlement_date.desc"

    @pytest.mark.asyncio
    async def test_api_error_captured(self):
        """HTTP errors should be captured in *_error keys, not raise."""
        import mcp_servers.price_data_mcp_server as mod

        with patch.object(mod._ginlix, "ensure", return_value=True), \
             patch.object(mod._ginlix, "request", new_callable=AsyncMock,
                          side_effect=httpx.HTTPStatusError(
                              "500", request=MagicMock(), response=MagicMock())):
            result = await mod.get_short_data("AAPL")

        assert "short_interest_error" in result or "short_volume_error" in result

    @pytest.mark.asyncio
    async def test_custom_limit(self):
        import mcp_servers.price_data_mcp_server as mod

        empty_resp = _make_response({"results": []})

        with patch.object(mod._ginlix, "ensure", return_value=True), \
             patch.object(mod._ginlix, "request", new_callable=AsyncMock,
                          return_value=empty_resp) as mock_req:
            await mod.get_short_data("GME", data_type="short_interest", limit=100)

        call_kwargs = mock_req.call_args
        params = call_kwargs.kwargs.get("params") or call_kwargs[1].get("params")
        assert params["limit"] == 100


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestNormalization:
    def test_normalize_ohlcv_descending(self):
        from data_client.normalize import normalize_bars

        rows = [
            {"date": "2025-01-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100},
            {"date": "2025-01-03", "open": 2, "high": 3, "low": 1, "close": 2.5, "volume": 200},
        ]
        result = normalize_bars(rows, "AAPL")
        assert result[0]["date"] == "2025-01-03"
        assert result[1]["date"] == "2025-01-01"

    def test_normalize_date_passthrough(self):
        from data_client.normalize import normalize_bars

        # Bars without a time field pass through the date string as-is
        rows = [{"date": "2025-01-01", "open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}]
        result = normalize_bars(rows, "AAPL")
        assert result[0]["date"] == "2025-01-01"

        empty = normalize_bars([{"open": 1, "high": 2, "low": 0.5, "close": 1.5, "volume": 100}], "AAPL")
        assert empty[0]["date"] == ""

    def test_as_float_handles_edge_cases(self):
        from data_client.normalize import _as_float

        assert _as_float(None) is None
        assert _as_float("10.5") == 10.5
        assert _as_float("not_a_number") is None
        assert _as_float(42) == 42.0

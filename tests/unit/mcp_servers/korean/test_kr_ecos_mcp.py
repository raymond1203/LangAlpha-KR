"""Tests for kr_ecos_mcp_server tools.

Tests all tools using mocked ECOS API (httpx) responses.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from mcp_servers.korean.kr_ecos_mcp_server import (
    get_kr_base_rate,
    get_kr_economic_indicator,
    get_kr_exchange_rate,
    get_kr_treasury_yield,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture(autouse=True)
def _mock_api_key():
    """Set ECOS_API_KEY for all tests."""
    with patch.dict("os.environ", {"ECOS_API_KEY": "test-key"}):
        yield


@pytest.fixture
def mock_ecos_response():
    """Standard ECOS API success response."""
    return {
        "StatisticSearch": {
            "list_total_count": 2,
            "row": [
                {
                    "TIME": "202401",
                    "DATA_VALUE": "3.50",
                    "STAT_NAME": "한국은행 기준금리",
                    "ITEM_NAME1": "기준금리",
                    "UNIT_NAME": "% p.a.",
                },
                {
                    "TIME": "202402",
                    "DATA_VALUE": "3.50",
                    "STAT_NAME": "한국은행 기준금리",
                    "ITEM_NAME1": "기준금리",
                    "UNIT_NAME": "% p.a.",
                },
            ],
        }
    }


@pytest.fixture
def mock_ecos_empty():
    """ECOS API response with no data."""
    return {"StatisticSearch": {"list_total_count": 0}}


@pytest.fixture
def mock_ecos_error():
    """ECOS API error response."""
    return {
        "RESULT": {
            "CODE": "ERROR-001",
            "MESSAGE": "인증키가 유효하지 않습니다.",
        }
    }


def _mock_httpx_get(response_data):
    """Create a mock httpx client that returns given response."""
    mock_response = MagicMock()
    mock_response.json.return_value = response_data
    mock_response.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_response
    return mock_client


# ============================================================================
# get_kr_base_rate
# ============================================================================


class TestGetKrBaseRate:
    @patch("mcp_servers.korean.kr_ecos_mcp_server.httpx.Client")
    def test_success(self, mock_client_cls, mock_ecos_response):
        mock_client_cls.return_value = _mock_httpx_get(mock_ecos_response)

        result = get_kr_base_rate("2024-01-01", "2024-02-28")

        assert result["data_type"] == "kr_base_rate"
        assert result["source"] == "ecos"
        assert result["count"] == 2
        assert result["data"][0]["date"] == "202401"
        assert result["data"][0]["value"] == 3.50

    @patch("mcp_servers.korean.kr_ecos_mcp_server.httpx.Client")
    def test_empty(self, mock_client_cls, mock_ecos_empty):
        mock_client_cls.return_value = _mock_httpx_get(mock_ecos_empty)

        result = get_kr_base_rate("2024-01-01", "2024-01-01")

        assert result["data"] == []
        assert result["count"] == 0

    @patch("mcp_servers.korean.kr_ecos_mcp_server.httpx.Client")
    def test_api_error(self, mock_client_cls, mock_ecos_error):
        mock_client_cls.return_value = _mock_httpx_get(mock_ecos_error)

        result = get_kr_base_rate("2024-01-01", "2024-02-28")

        assert "error" in result

    def test_missing_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            result = get_kr_base_rate("2024-01-01", "2024-02-28")

            assert "error" in result
            assert "ECOS_API_KEY" in result["error"]


# ============================================================================
# get_kr_economic_indicator
# ============================================================================


class TestGetKrEconomicIndicator:
    @patch("mcp_servers.korean.kr_ecos_mcp_server.httpx.Client")
    def test_gdp(self, mock_client_cls, mock_ecos_response):
        mock_client_cls.return_value = _mock_httpx_get(mock_ecos_response)

        result = get_kr_economic_indicator("gdp_growth", "2023-01-01", "2024-12-31")

        assert result["data_type"] == "kr_economic_indicator"
        assert result["indicator"] == "gdp_growth"
        assert result["count"] == 2

    @patch("mcp_servers.korean.kr_ecos_mcp_server.httpx.Client")
    def test_cpi(self, mock_client_cls, mock_ecos_response):
        mock_client_cls.return_value = _mock_httpx_get(mock_ecos_response)

        result = get_kr_economic_indicator("cpi", "2024-01-01", "2024-12-31")

        assert result["indicator"] == "cpi"

    def test_unknown_indicator(self):
        result = get_kr_economic_indicator("unknown", "2024-01-01", "2024-12-31")

        assert "error" in result
        assert "알 수 없는 지표" in result["error"]

    @patch("mcp_servers.korean.kr_ecos_mcp_server.httpx.Client")
    def test_cycle_override(self, mock_client_cls, mock_ecos_response):
        mock_client = _mock_httpx_get(mock_ecos_response)
        mock_client_cls.return_value = mock_client

        get_kr_economic_indicator("cpi", "2024-01-01", "2024-12-31", cycle="A")

        call_url = mock_client.get.call_args[0][0]
        assert "/A/" in call_url


# ============================================================================
# get_kr_exchange_rate
# ============================================================================


class TestGetKrExchangeRate:
    @patch("mcp_servers.korean.kr_ecos_mcp_server.httpx.Client")
    def test_usd(self, mock_client_cls, mock_ecos_response):
        mock_client_cls.return_value = _mock_httpx_get(mock_ecos_response)

        result = get_kr_exchange_rate("USD", "2024-01-01", "2024-12-31")

        assert result["data_type"] == "kr_exchange_rate"
        assert result["currency"] == "USD"

    @patch("mcp_servers.korean.kr_ecos_mcp_server.httpx.Client")
    def test_jpy(self, mock_client_cls, mock_ecos_response):
        mock_client_cls.return_value = _mock_httpx_get(mock_ecos_response)

        result = get_kr_exchange_rate("jpy", "2024-01-01", "2024-12-31")

        assert result["currency"] == "JPY"

    def test_unknown_currency(self):
        result = get_kr_exchange_rate("XYZ", "2024-01-01", "2024-12-31")

        assert "error" in result
        assert "알 수 없는 통화" in result["error"]


# ============================================================================
# get_kr_treasury_yield
# ============================================================================


class TestGetKrTreasuryYield:
    @patch("mcp_servers.korean.kr_ecos_mcp_server.httpx.Client")
    def test_10y(self, mock_client_cls, mock_ecos_response):
        mock_client_cls.return_value = _mock_httpx_get(mock_ecos_response)

        result = get_kr_treasury_yield("10Y", "2024-01-01", "2024-12-31")

        assert result["data_type"] == "kr_treasury_yield"
        assert result["maturity"] == "10Y"
        assert result["count"] == 2

    @patch("mcp_servers.korean.kr_ecos_mcp_server.httpx.Client")
    def test_3y(self, mock_client_cls, mock_ecos_response):
        mock_client_cls.return_value = _mock_httpx_get(mock_ecos_response)

        result = get_kr_treasury_yield("3y", "2024-01-01", "2024-12-31")

        assert result["maturity"] == "3Y"

    def test_unknown_maturity(self):
        result = get_kr_treasury_yield("99Y", "2024-01-01", "2024-12-31")

        assert "error" in result
        assert "알 수 없는 만기" in result["error"]

    @patch("mcp_servers.korean.kr_ecos_mcp_server.httpx.Client")
    def test_http_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.side_effect = httpx.HTTPError("connection failed")
        mock_client_cls.return_value = mock_client

        result = get_kr_treasury_yield("10Y", "2024-01-01", "2024-12-31")

        assert "error" in result

    def test_empty_dates(self):
        result = get_kr_treasury_yield("10Y", from_date="", to_date="")

        assert "error" in result
        assert "필수" in result["error"]


# ============================================================================
# Empty date validation
# ============================================================================


class TestEmptyDateValidation:
    def test_exchange_rate_empty_dates(self):
        result = get_kr_exchange_rate("USD")

        assert "error" in result
        assert "필수" in result["error"]

    def test_exchange_rate_missing_to_date(self):
        result = get_kr_exchange_rate("USD", from_date="2024-01-01")

        assert "error" in result

    def test_treasury_empty_dates(self):
        result = get_kr_treasury_yield("10Y")

        assert "error" in result
        assert "필수" in result["error"]


# ============================================================================
# API key masking
# ============================================================================


class TestApiKeyMasking:
    @patch("mcp_servers.korean.kr_ecos_mcp_server.httpx.Client")
    def test_key_not_in_error_message(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        # Simulate error containing the API key in the URL
        mock_client.get.side_effect = httpx.HTTPError(
            "GET https://ecos.bok.or.kr/api/StatisticSearch/test-key/json/kr/... failed"
        )
        mock_client_cls.return_value = mock_client

        result = get_kr_base_rate("2024-01-01", "2024-12-31")

        assert "error" in result
        assert "test-key" not in result["error"]
        assert "***" in result["error"]

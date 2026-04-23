"""Tests for kr_price_mcp_server tools.

Tests all tools using mocked pykrx responses: success, empty data, and exceptions.
"""

from unittest.mock import patch

import pandas as pd
import pytest

from mcp_servers.korean.kr_price_mcp_server import (
    get_kr_fundamental,
    get_kr_market_cap,
    get_kr_market_snapshot,
    get_kr_stock_ohlcv,
    search_kr_ticker,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_ohlcv_df():
    """OHLCV DataFrame matching pykrx output format."""
    dates = pd.DatetimeIndex(["2024-01-02", "2024-01-03", "2024-01-04"])
    return pd.DataFrame(
        {
            "시가": [75000, 75500, 76000],
            "고가": [76000, 76500, 77000],
            "저가": [74500, 75000, 75500],
            "종가": [75500, 76000, 76500],
            "거래량": [10000000, 12000000, 11000000],
            "거래대금": [750000000000, 912000000000, 841500000000],
            "등락률": [0.67, 0.66, 0.66],
        },
        index=dates,
    )


@pytest.fixture
def mock_market_cap_df():
    """Market cap DataFrame matching pykrx output format."""
    dates = pd.DatetimeIndex(["2024-01-02", "2024-01-03"])
    return pd.DataFrame(
        {
            "시가총액": [450000000000000, 453000000000000],
            "거래량": [10000000, 12000000],
            "거래대금": [750000000000, 912000000000],
            "상장주식수": [5969782550, 5969782550],
        },
        index=dates,
    )


@pytest.fixture
def mock_fundamental_df():
    """Fundamental DataFrame matching pykrx output format."""
    dates = pd.DatetimeIndex(["2024-01-02", "2024-01-03"])
    return pd.DataFrame(
        {
            "BPS": [37528, 37528],
            "PER": [26.22, 26.50],
            "PBR": [2.21, 2.24],
            "EPS": [3166, 3166],
            "DIV": [1.71, 1.69],
            "DPS": [1416, 1416],
        },
        index=dates,
    )


@pytest.fixture
def mock_snapshot_df():
    """Full-market OHLCV snapshot (ticker-indexed)."""
    return pd.DataFrame(
        {
            "시가": [75000, 130000],
            "고가": [76000, 132000],
            "저가": [74500, 129000],
            "종가": [75500, 131000],
            "거래량": [10000000, 5000000],
            "거래대금": [750000000000, 650000000000],
            "등락률": [0.67, 0.77],
        },
        index=["005930", "000660"],
    )


# ============================================================================
# get_kr_stock_ohlcv
# ============================================================================


class TestGetKrStockOhlcv:
    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_success(self, mock_stock, mock_ohlcv_df):
        mock_stock.get_market_ohlcv.return_value = mock_ohlcv_df

        result = get_kr_stock_ohlcv("005930", "2024-01-02", "2024-01-04")

        assert result["data_type"] == "kr_stock_ohlcv"
        assert result["source"] == "pykrx"
        assert result["count"] == 3
        assert result["ticker"] == "005930"

        first = result["data"][0]
        assert first["date"] == "2024-01-02"
        assert first["open"] == 75000
        assert first["close"] == 75500
        assert first["volume"] == 10000000
        assert first["trading_value"] == 750000000000

        mock_stock.get_market_ohlcv.assert_called_once_with(
            "20240102", "20240104", "005930", adjusted=True, freq="d"
        )

    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_hyphen_date_normalized(self, mock_stock, mock_ohlcv_df):
        mock_stock.get_market_ohlcv.return_value = mock_ohlcv_df

        get_kr_stock_ohlcv("005930", "2024-01-02", "2024-01-04")

        mock_stock.get_market_ohlcv.assert_called_once_with(
            "20240102", "20240104", "005930", adjusted=True, freq="d"
        )

    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_empty_data(self, mock_stock):
        mock_stock.get_market_ohlcv.return_value = pd.DataFrame()

        result = get_kr_stock_ohlcv("999999", "2024-01-02", "2024-01-04")

        assert "error" in result

    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_exception(self, mock_stock):
        mock_stock.get_market_ohlcv.side_effect = RuntimeError("network error")

        result = get_kr_stock_ohlcv("005930", "2024-01-02", "2024-01-04")

        assert "error" in result
        assert "network error" in result["error"]

    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_monthly_frequency(self, mock_stock, mock_ohlcv_df):
        mock_stock.get_market_ohlcv.return_value = mock_ohlcv_df

        get_kr_stock_ohlcv("005930", "20240101", "20240401", freq="m")

        mock_stock.get_market_ohlcv.assert_called_once_with(
            "20240101", "20240401", "005930", adjusted=True, freq="m"
        )


# ============================================================================
# get_kr_market_cap
# ============================================================================


class TestGetKrMarketCap:
    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_success(self, mock_stock, mock_market_cap_df):
        mock_stock.get_market_cap.return_value = mock_market_cap_df

        result = get_kr_market_cap("005930", "2024-01-02", "2024-01-03")

        assert result["data_type"] == "kr_market_cap"
        assert result["count"] == 2

        first = result["data"][0]
        assert first["market_cap"] == 450000000000000
        assert first["listed_shares"] == 5969782550

    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_empty_data(self, mock_stock):
        mock_stock.get_market_cap.return_value = pd.DataFrame()

        result = get_kr_market_cap("999999", "2024-01-02", "2024-01-03")

        assert "error" in result

    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_exception(self, mock_stock):
        mock_stock.get_market_cap.side_effect = ValueError("bad ticker")

        result = get_kr_market_cap("005930", "2024-01-02", "2024-01-03")

        assert "error" in result


# ============================================================================
# get_kr_fundamental
# ============================================================================


class TestGetKrFundamental:
    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_success(self, mock_stock, mock_fundamental_df):
        mock_stock.get_market_fundamental.return_value = mock_fundamental_df

        result = get_kr_fundamental("005930", "2024-01-02", "2024-01-03")

        assert result["data_type"] == "kr_fundamental"
        assert result["count"] == 2

        first = result["data"][0]
        assert first["per"] == 26.22
        assert first["pbr"] == 2.21
        assert first["eps"] == 3166.0
        assert first["div"] == 1.71

    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_empty_data(self, mock_stock):
        mock_stock.get_market_fundamental.return_value = pd.DataFrame()

        result = get_kr_fundamental("999999", "2024-01-02", "2024-01-03")

        assert "error" in result


# ============================================================================
# search_kr_ticker
# ============================================================================


class TestSearchKrTicker:
    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_search_by_name(self, mock_stock):
        mock_stock.get_market_ticker_list.return_value = [
            "005930", "005935", "000660",
        ]
        mock_stock.get_market_ticker_name.side_effect = lambda t: {
            "005930": "삼성전자",
            "005935": "삼성전자우",
            "000660": "SK하이닉스",
        }[t]

        result = search_kr_ticker("삼성")

        assert result["data_type"] == "kr_ticker_search"
        assert result["count"] == 2
        assert result["data"][0]["ticker"] == "005930"
        assert result["data"][0]["name"] == "삼성전자"
        assert result["data"][1]["ticker"] == "005935"

    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_list_all(self, mock_stock):
        mock_stock.get_market_ticker_list.return_value = ["005930", "000660"]
        mock_stock.get_market_ticker_name.side_effect = lambda t: {
            "005930": "삼성전자",
            "000660": "SK하이닉스",
        }[t]

        result = search_kr_ticker("*", market="KOSPI")

        assert result["count"] == 2
        mock_stock.get_market_ticker_list.assert_called_once_with(market="KOSPI")

    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_no_match(self, mock_stock):
        mock_stock.get_market_ticker_list.return_value = ["005930"]
        mock_stock.get_market_ticker_name.return_value = "삼성전자"

        result = search_kr_ticker("존재하지않는기업")

        assert result["count"] == 0
        assert result["data"] == []

    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_with_date(self, mock_stock):
        mock_stock.get_market_ticker_list.return_value = []

        search_kr_ticker("삼성", date="2024-01-02")

        mock_stock.get_market_ticker_list.assert_called_once_with(
            "20240102", market="ALL"
        )

    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_exception(self, mock_stock):
        mock_stock.get_market_ticker_list.side_effect = RuntimeError("fail")

        result = search_kr_ticker("삼성")

        assert "error" in result


# ============================================================================
# get_kr_market_snapshot
# ============================================================================


class TestGetKrMarketSnapshot:
    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_success(self, mock_stock, mock_snapshot_df):
        mock_stock.get_market_ohlcv.return_value = mock_snapshot_df

        result = get_kr_market_snapshot("2024-01-02", market="KOSPI")

        assert result["data_type"] == "kr_market_snapshot"
        assert result["count"] == 2
        assert result["market"] == "KOSPI"

        first = result["data"][0]
        assert first["ticker"] == "005930"
        assert first["close"] == 75500

    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_empty_market(self, mock_stock):
        mock_stock.get_market_ohlcv.return_value = pd.DataFrame()

        result = get_kr_market_snapshot("2024-12-25")

        assert "error" in result

    @patch("mcp_servers.korean.kr_price_mcp_server.stock")
    def test_exception(self, mock_stock):
        mock_stock.get_market_ohlcv.side_effect = RuntimeError("fail")

        result = get_kr_market_snapshot("2024-01-02")

        assert "error" in result

"""Tests for KoreanDataSource.

Tests the MarketDataSource protocol implementation using mocked pykrx responses.
"""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from src.data_client.korean.data_source import (
    KoreanDataSource,
    _strip_suffix,
)


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def source():
    return KoreanDataSource()


@pytest.fixture
def mock_ohlcv_df():
    """pykrx OHLCV DataFrame with KST timestamps."""
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
def mock_ohlcv_with_nan():
    dates = pd.DatetimeIndex(["2024-01-02"])
    return pd.DataFrame(
        {
            "시가": [np.nan],
            "고가": [np.nan],
            "저가": [np.nan],
            "종가": [np.nan],
            "거래량": [0],
            "거래대금": [np.nan],
            "등락률": [np.nan],
        },
        index=dates,
    )


@pytest.fixture
def mock_single_row_df():
    """Single-row DataFrame — no previous close available."""
    dates = pd.DatetimeIndex(["2024-01-04"])
    return pd.DataFrame(
        {
            "시가": [76000],
            "고가": [77000],
            "저가": [75500],
            "종가": [76500],
            "거래량": [11000000],
            "거래대금": [841500000000],
            "등락률": [0.66],
        },
        index=dates,
    )


# ============================================================================
# _strip_suffix
# ============================================================================


class TestStripSuffix:
    def test_ks_suffix(self):
        assert _strip_suffix("005930.KS") == "005930"

    def test_kq_suffix(self):
        assert _strip_suffix("263750.KQ") == "263750"

    def test_lowercase(self):
        assert _strip_suffix("005930.ks") == "005930"

    def test_mixed_case(self):
        assert _strip_suffix("005930.Ks") == "005930"
        assert _strip_suffix("263750.kQ") == "263750"

    def test_no_suffix(self):
        assert _strip_suffix("005930") == "005930"

    def test_other_suffix(self):
        assert _strip_suffix("AAPL.US") == "AAPL.US"


# ============================================================================
# get_daily
# ============================================================================


class TestGetDaily:
    @pytest.mark.asyncio
    @patch("src.data_client.korean.data_source.stock")
    async def test_success(self, mock_stock, source, mock_ohlcv_df):
        mock_stock.get_market_ohlcv.return_value = mock_ohlcv_df

        result = await source.get_daily(
            "005930.KS", from_date="2024-01-02", to_date="2024-01-04"
        )

        assert len(result) == 3
        bar = result[0]
        assert "time" in bar
        assert bar["time"] > 0
        assert bar["open"] == 75000.0
        assert bar["close"] == 75500.0
        assert bar["volume"] == 10000000

        mock_stock.get_market_ohlcv.assert_called_once_with(
            "20240102", "20240104", "005930"
        )

    @pytest.mark.asyncio
    @patch("src.data_client.korean.data_source.stock")
    async def test_strips_suffix(self, mock_stock, source):
        mock_stock.get_market_ohlcv.return_value = pd.DataFrame()

        await source.get_daily("263750.KQ", from_date="2024-01-02", to_date="2024-01-04")

        mock_stock.get_market_ohlcv.assert_called_once_with(
            "20240102", "20240104", "263750"
        )

    @pytest.mark.asyncio
    @patch("src.data_client.korean.data_source.stock")
    async def test_empty_returns_empty_list(self, mock_stock, source):
        mock_stock.get_market_ohlcv.return_value = pd.DataFrame()

        result = await source.get_daily("999999.KS")

        assert result == []

    @pytest.mark.asyncio
    @patch("src.data_client.korean.data_source.stock")
    async def test_nan_values_safe(self, mock_stock, source, mock_ohlcv_with_nan):
        mock_stock.get_market_ohlcv.return_value = mock_ohlcv_with_nan

        result = await source.get_daily("005930.KS", from_date="2024-01-02", to_date="2024-01-02")

        assert len(result) == 1
        bar = result[0]
        assert bar["open"] == 0.0
        assert bar["volume"] == 0


# ============================================================================
# get_intraday
# ============================================================================


class TestGetIntraday:
    @pytest.mark.asyncio
    async def test_raises_for_any_interval(self, source):
        with pytest.raises(ValueError, match="not supported by pykrx"):
            await source.get_intraday("005930.KS", interval="1min")

    @pytest.mark.asyncio
    async def test_raises_for_5min(self, source):
        with pytest.raises(ValueError):
            await source.get_intraday("005930.KS", interval="5min")


# ============================================================================
# get_market_status
# ============================================================================


class TestGetMarketStatus:
    @pytest.mark.asyncio
    async def test_returns_status(self, source):
        result = await source.get_market_status()

        assert result["market"] in ("open", "closed")
        assert "serverTime" in result


# ============================================================================
# get_snapshots
# ============================================================================


class TestGetSnapshots:
    @pytest.mark.asyncio
    @patch("src.data_client.korean.data_source.stock")
    async def test_empty_symbols(self, mock_stock, source):
        result = await source.get_snapshots([])

        assert result == []
        mock_stock.get_market_ohlcv.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.data_client.korean.data_source.stock")
    async def test_ks_symbol_preserved(self, mock_stock, source, mock_ohlcv_df):
        mock_stock.get_market_ohlcv.return_value = mock_ohlcv_df

        result = await source.get_snapshots(["005930.KS"])

        assert len(result) == 1
        assert result[0]["symbol"] == "005930.KS"
        assert result[0]["price"] > 0

    @pytest.mark.asyncio
    @patch("src.data_client.korean.data_source.stock")
    async def test_kq_symbol_preserved(self, mock_stock, source, mock_ohlcv_df):
        mock_stock.get_market_ohlcv.return_value = mock_ohlcv_df

        result = await source.get_snapshots(["263750.KQ"])

        assert len(result) == 1
        assert result[0]["symbol"] == "263750.KQ"

    @pytest.mark.asyncio
    @patch("src.data_client.korean.data_source.stock")
    async def test_previous_close_with_multi_row(self, mock_stock, source, mock_ohlcv_df):
        mock_stock.get_market_ohlcv.return_value = mock_ohlcv_df

        result = await source.get_snapshots(["005930.KS"])

        snap = result[0]
        # Last row close=76500, second-to-last close=76000
        assert snap["price"] == 76500.0
        assert snap["previous_close"] == 76000.0
        assert snap["change"] == 500.0

    @pytest.mark.asyncio
    @patch("src.data_client.korean.data_source.stock")
    async def test_single_row_no_previous_close(self, mock_stock, source, mock_single_row_df):
        mock_stock.get_market_ohlcv.return_value = mock_single_row_df

        result = await source.get_snapshots(["005930.KS"])

        snap = result[0]
        assert snap["previous_close"] == 0.0
        assert snap["change"] == 0.0
        assert snap["change_percent"] == 0.0

    @pytest.mark.asyncio
    @patch("src.data_client.korean.data_source.stock")
    async def test_failed_ticker_skipped(self, mock_stock, source):
        mock_stock.get_market_ohlcv.side_effect = RuntimeError("network error")

        result = await source.get_snapshots(["005930.KS"])

        assert result == []

    @pytest.mark.asyncio
    @patch("src.data_client.korean.data_source.stock")
    async def test_indices_asset_type_raises_to_trigger_chain_fallback(
        self, mock_stock, source
    ):
        # KoreanDataSource 는 인덱스 snapshot 을 지원하지 않음. 빈 list 를 반환하면
        # MarketDataProvider 가 yfinance 로 fallback 하지 않으므로 명시적으로 raise.
        with pytest.raises(NotImplementedError, match="indices"):
            await source.get_snapshots(["KS11", "KQ11"], asset_type="indices")
        # pykrx 는 호출되지 않아야 함 (가드가 먼저 작동)
        mock_stock.get_market_ohlcv.assert_not_called()

    @pytest.mark.asyncio
    @patch("src.data_client.korean.data_source.stock")
    async def test_unknown_asset_type_raises(self, mock_stock, source):
        with pytest.raises(NotImplementedError):
            await source.get_snapshots(["X"], asset_type="forex")
        mock_stock.get_market_ohlcv.assert_not_called()

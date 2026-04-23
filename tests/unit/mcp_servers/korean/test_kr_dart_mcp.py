"""Tests for kr_dart_mcp_server tools.

Tests all tools using mocked OpenDartReader responses.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from mcp_servers.korean.kr_dart_mcp_server import (
    _df_to_records,
    get_dart_company_info,
    get_dart_disclosures,
    get_dart_financials,
    get_dart_financials_all,
    get_dart_major_shareholders,
    search_dart_corp,
)


# ============================================================================
# _df_to_records
# ============================================================================


class TestDfToRecords:
    def test_nan_converted_to_none(self):
        df = pd.DataFrame({
            "name": ["삼성전자", "SK하이닉스"],
            "value": [100.5, np.nan],
            "count": [10, np.nan],
        })

        records = _df_to_records(df)

        assert len(records) == 2
        assert records[0]["name"] == "삼성전자"
        assert records[0]["value"] == 100.5
        assert records[1]["value"] is None
        assert records[1]["count"] is None

    def test_empty_dataframe(self):
        assert _df_to_records(pd.DataFrame()) == []

    def test_none_input(self):
        assert _df_to_records(None) == []


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def mock_dart():
    """Mock OpenDartReader instance."""
    with patch("mcp_servers.korean.kr_dart_mcp_server._get_dart") as mock_get:
        dart = MagicMock()
        mock_get.return_value = dart
        yield dart


@pytest.fixture
def mock_company_info():
    return {
        "corp_code": "00126380",
        "corp_name": "삼성전자",
        "stock_code": "005930",
        "ceo_nm": "한종희",
        "corp_cls": "Y",
        "adres": "경기도 수원시 영통구 삼성로 129",
    }


@pytest.fixture
def mock_finstate_df():
    return pd.DataFrame({
        "rcept_no": ["20240401000001", "20240401000001"],
        "bsns_year": ["2023", "2023"],
        "corp_code": ["00126380", "00126380"],
        "account_nm": ["매출액", "영업이익"],
        "thstrm_amount": ["258935000000000", "36183000000000"],
        "frmtrm_amount": ["302231000000000", "43376000000000"],
    })


@pytest.fixture
def mock_disclosure_df():
    return pd.DataFrame({
        "corp_name": ["삼성전자", "삼성전자"],
        "report_nm": ["사업보고서 (2023.12)", "[기재정정]주요사항보고서"],
        "rcept_no": ["20240401000001", "20240315000002"],
        "rcept_dt": ["2024-04-01", "2024-03-15"],
        "flr_nm": ["삼성전자", "삼성전자"],
    })


@pytest.fixture
def mock_shareholders_df():
    return pd.DataFrame({
        "rcept_no": ["20240301000001"],
        "corp_name": ["삼성전자"],
        "report_tp": ["대량보유상황보고"],
        "repror": ["국민연금공단"],
        "stkqy": ["500000000"],
        "stkrt": ["8.37"],
    })


# ============================================================================
# search_dart_corp
# ============================================================================


class TestSearchDartCorp:
    def test_search_by_name(self, mock_dart):
        mock_dart.company_by_name.return_value = pd.DataFrame({
            "corp_code": ["00126380", "00164779"],
            "corp_name": ["삼성전자", "삼성전자우"],
            "stock_code": ["005930", "005935"],
        })

        result = search_dart_corp("삼성전자")

        assert result["data_type"] == "dart_company_search"
        assert result["count"] == 2
        assert result["data"][0]["corp_name"] == "삼성전자"
        mock_dart.company_by_name.assert_called_once_with("삼성전자")

    def test_search_by_stock_code(self, mock_dart, mock_company_info):
        mock_dart.company.return_value = mock_company_info

        result = search_dart_corp("005930")

        assert result["count"] == 1
        assert result["data"][0]["corp_name"] == "삼성전자"
        mock_dart.company.assert_called_once_with("005930")

    def test_no_results(self, mock_dart):
        mock_dart.company_by_name.return_value = None

        result = search_dart_corp("존재하지않는기업")

        assert result["data"] == []
        assert result["count"] == 0

    def test_exception(self, mock_dart):
        mock_dart.company_by_name.side_effect = RuntimeError("API error")

        result = search_dart_corp("삼성전자")

        assert "error" in result


# ============================================================================
# get_dart_company_info
# ============================================================================


class TestGetDartCompanyInfo:
    def test_success(self, mock_dart, mock_company_info):
        mock_dart.company.return_value = mock_company_info

        result = get_dart_company_info("005930")

        assert result["data_type"] == "dart_company_info"
        assert result["data"]["corp_name"] == "삼성전자"

    def test_not_found(self, mock_dart):
        mock_dart.company.return_value = None

        result = get_dart_company_info("999999")

        assert result["data"] == {}

    def test_exception(self, mock_dart):
        mock_dart.company.side_effect = RuntimeError("fail")

        result = get_dart_company_info("005930")

        assert "error" in result


# ============================================================================
# get_dart_financials
# ============================================================================


class TestGetDartFinancials:
    def test_success(self, mock_dart, mock_finstate_df):
        mock_dart.finstate.return_value = mock_finstate_df

        result = get_dart_financials("삼성전자", 2023)

        assert result["data_type"] == "dart_financials"
        assert result["count"] == 2
        assert result["year"] == 2023
        assert result["reprt_code"] == "11011"
        assert result["data"][0]["account_nm"] == "매출액"

    def test_quarterly(self, mock_dart, mock_finstate_df):
        mock_dart.finstate.return_value = mock_finstate_df

        get_dart_financials("005930", 2023, reprt_code="11013")

        mock_dart.finstate.assert_called_once_with("005930", 2023, reprt_code="11013")

    def test_empty(self, mock_dart):
        mock_dart.finstate.return_value = pd.DataFrame()

        result = get_dart_financials("005930", 2023)

        assert result["data"] == []
        assert result["count"] == 0

    def test_multiple_corps(self, mock_dart, mock_finstate_df):
        mock_dart.finstate.return_value = mock_finstate_df

        get_dart_financials("005930, 000660", 2023)

        mock_dart.finstate.assert_called_once_with("005930, 000660", 2023, reprt_code="11011")

    def test_exception(self, mock_dart):
        mock_dart.finstate.side_effect = RuntimeError("API limit")

        result = get_dart_financials("005930", 2023)

        assert "error" in result


# ============================================================================
# get_dart_financials_all
# ============================================================================


class TestGetDartFinancialsAll:
    def test_success(self, mock_dart, mock_finstate_df):
        mock_dart.finstate_all.return_value = mock_finstate_df

        result = get_dart_financials_all("005930", 2023)

        assert result["data_type"] == "dart_financials_all"
        assert result["count"] == 2

    def test_empty(self, mock_dart):
        mock_dart.finstate_all.return_value = None

        result = get_dart_financials_all("005930", 2023)

        assert result["data"] == []

    def test_exception(self, mock_dart):
        mock_dart.finstate_all.side_effect = RuntimeError("fail")

        result = get_dart_financials_all("005930", 2023)

        assert "error" in result


# ============================================================================
# get_dart_disclosures
# ============================================================================


class TestGetDartDisclosures:
    def test_all_disclosures(self, mock_dart, mock_disclosure_df):
        mock_dart.list.return_value = mock_disclosure_df

        result = get_dart_disclosures("005930")

        assert result["data_type"] == "dart_disclosures"
        assert result["count"] == 2
        assert result["kind"] == "all"

    def test_filtered_by_kind(self, mock_dart, mock_disclosure_df):
        mock_dart.list.return_value = mock_disclosure_df

        get_dart_disclosures("005930", kind="A")

        mock_dart.list.assert_called_once_with("005930", kind="A")

    def test_with_date_range(self, mock_dart, mock_disclosure_df):
        mock_dart.list.return_value = mock_disclosure_df

        get_dart_disclosures("삼성전자", start="2024-01-01", end="2024-12-31", kind="B")

        mock_dart.list.assert_called_once_with(
            "삼성전자", start="2024-01-01", end="2024-12-31", kind="B",
        )

    def test_empty(self, mock_dart):
        mock_dart.list.return_value = pd.DataFrame()

        result = get_dart_disclosures("999999")

        assert result["data"] == []

    def test_exception(self, mock_dart):
        mock_dart.list.side_effect = RuntimeError("fail")

        result = get_dart_disclosures("005930")

        assert "error" in result


# ============================================================================
# get_dart_major_shareholders
# ============================================================================


class TestGetDartMajorShareholders:
    def test_success(self, mock_dart, mock_shareholders_df):
        mock_dart.major_shareholders.return_value = mock_shareholders_df

        result = get_dart_major_shareholders("삼성전자")

        assert result["data_type"] == "dart_major_shareholders"
        assert result["count"] == 1
        assert result["data"][0]["repror"] == "국민연금공단"

    def test_empty(self, mock_dart):
        mock_dart.major_shareholders.return_value = pd.DataFrame()

        result = get_dart_major_shareholders("005930")

        assert result["data"] == []

    def test_exception(self, mock_dart):
        mock_dart.major_shareholders.side_effect = RuntimeError("fail")

        result = get_dart_major_shareholders("005930")

        assert "error" in result

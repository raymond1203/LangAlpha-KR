"""Tests for src/tools/search.py — search engine selection and tool creation.

Tests the get_web_search_tool factory function's routing logic and
validation, plus the ToolUsageTracker used by search tool wrappers.
"""

from unittest.mock import MagicMock, patch

import pytest

from src.config.tools import SearchEngine
from src.tools.decorators import (
    ToolUsageTracker,
    start_tool_tracking,
    stop_tool_tracking,
    get_tool_tracker,
)


# ---------------------------------------------------------------------------
# Tests for SearchEngine enum
# ---------------------------------------------------------------------------


class TestSearchEngineEnum:
    """Tests for SearchEngine enum values."""

    def test_tavily_value(self):
        assert SearchEngine.TAVILY.value == "tavily"

    def test_serper_value(self):
        assert SearchEngine.SERPER.value == "serper"

    def test_bocha_value(self):
        assert SearchEngine.BOCHA.value == "bocha"

    def test_all_members(self):
        members = [e.value for e in SearchEngine]
        assert "tavily" in members
        assert "serper" in members
        assert "bocha" in members


# ---------------------------------------------------------------------------
# Tests for get_web_search_tool routing
# ---------------------------------------------------------------------------


class TestGetWebSearchToolRouting:
    """Tests for get_web_search_tool engine selection routing."""

    def test_serper_engine_calls_serper_configure(self):
        """When SELECTED_SEARCH_ENGINE is serper, serper configure is called."""
        mock_configure = MagicMock()
        mock_web_search = MagicMock()
        mock_tool = MagicMock()

        mock_serper_module = MagicMock(
            configure=mock_configure,
            web_search=mock_web_search,
        )

        with (
            patch("src.tools.search.SELECTED_SEARCH_ENGINE", SearchEngine.SERPER.value),
            patch.dict("sys.modules", {"src.tools.search_services.serper": mock_serper_module}),
            patch("src.tools.search.create_logged_tool", return_value=mock_tool) as mock_create,
        ):
            from src.tools.search import get_web_search_tool
            result = get_web_search_tool(max_search_results=5, time_range="w")

        mock_configure.assert_called_once_with(max_results=5, default_time_range="w")
        mock_create.assert_called_once()
        assert result == mock_tool

    def test_unsupported_engine_raises(self):
        """An unknown engine string raises ValueError."""
        with patch("src.tools.search.SELECTED_SEARCH_ENGINE", "unknown_engine"):
            from src.tools.search import get_web_search_tool
            with pytest.raises(ValueError, match="Unsupported search engine"):
                get_web_search_tool(max_search_results=5)

    def test_tavily_engine_calls_tavily_configure(self):
        """When SELECTED_SEARCH_ENGINE is tavily, tavily configure is called."""
        mock_configure = MagicMock()
        mock_web_search = MagicMock()
        mock_tool = MagicMock()

        mock_tavily_module = MagicMock(
            configure=mock_configure,
            web_search=mock_web_search,
        )

        with (
            patch("src.tools.search.SELECTED_SEARCH_ENGINE", SearchEngine.TAVILY.value),
            patch.dict("sys.modules", {"src.tools.search_services.tavily": mock_tavily_module}),
            patch("src.tools.search.create_logged_tool", return_value=mock_tool) as mock_create,
        ):
            from src.tools.search import get_web_search_tool
            result = get_web_search_tool(
                max_search_results=10, time_range="m", verbose=False
            )

        mock_configure.assert_called_once_with(
            max_results=10, default_time_range="m", verbose=False
        )


# ---------------------------------------------------------------------------
# Tests for ToolUsageTracker
# ---------------------------------------------------------------------------


class TestToolUsageTracker:
    """Tests for the ToolUsageTracker used by search tool wrappers."""

    def test_record_usage_increments(self):
        tracker = ToolUsageTracker()
        tracker.record_usage("SerperSearchTool", count=1)
        tracker.record_usage("SerperSearchTool", count=2)
        assert tracker.usage["SerperSearchTool"] == 3

    def test_get_summary(self):
        tracker = ToolUsageTracker()
        tracker.record_usage("ToolA", count=5)
        summary = tracker.get_summary()
        assert isinstance(summary, dict)
        assert summary["ToolA"] == 5

    def test_reset_clears_usage(self):
        tracker = ToolUsageTracker()
        tracker.record_usage("ToolA", count=3)
        tracker.reset()
        assert tracker.get_summary() == {}

    def test_zero_count_not_recorded(self):
        tracker = ToolUsageTracker()
        tracker.record_usage("ToolA", count=0)
        assert "ToolA" not in tracker.usage

    def test_repr(self):
        tracker = ToolUsageTracker()
        tracker.record_usage("A", 2)
        tracker.record_usage("B", 3)
        r = repr(tracker)
        assert "tools=2" in r
        assert "total_calls=5" in r


class TestToolTrackingContextVar:
    """Tests for start/stop/get tool tracking via ContextVar."""

    def test_start_and_get(self):
        tracker = start_tool_tracking()
        assert get_tool_tracker() is tracker
        # Cleanup
        stop_tool_tracking()

    def test_stop_returns_summary(self):
        tracker = start_tool_tracking()
        tracker.record_usage("SearchTool", 2)
        summary = stop_tool_tracking()
        assert summary == {"SearchTool": 2}
        # After stop, tracker should be gone
        assert get_tool_tracker() is None

    def test_stop_without_start_returns_none(self):
        # Ensure no tracker is active
        stop_tool_tracking()
        result = stop_tool_tracking()
        assert result is None

"""Tests for the LargeResultEvictionMiddleware.

Verifies content preview creation, extension detection, size-based eviction,
and excluded-tool bypass logic.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import ToolMessage

from ptc_agent.agent.middleware.large_result_eviction import (
    NUM_CHARS_PER_TOKEN,
    TOOLS_EXCLUDED_FROM_EVICTION,
    LargeResultEvictionMiddleware,
    _create_content_preview,
    _detect_extension,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_backend(write_error: str | None = None) -> MagicMock:
    """Create a mock backend implementing the write interface."""
    backend = MagicMock()
    write_result = MagicMock()
    write_result.error = write_error
    write_result.path = "/evicted/file.md" if not write_error else None
    write_result.files_update = {"path": "data"} if not write_error else None
    backend.write = MagicMock(return_value=write_result)

    awrite_result = MagicMock()
    awrite_result.error = write_error
    awrite_result.path = "/evicted/file.md" if not write_error else None
    awrite_result.files_update = {"path": "data"} if not write_error else None
    backend.awrite = AsyncMock(return_value=awrite_result)
    return backend


def _make_tool_request(tool_name: str = "CustomTool") -> MagicMock:
    """Create a mock ToolCallRequest."""
    request = MagicMock()
    request.tool_call = {"name": tool_name}
    request.runtime = MagicMock()
    return request


# ---------------------------------------------------------------------------
# Tests for pure functions
# ---------------------------------------------------------------------------


class TestCreateContentPreview:
    """Tests for _create_content_preview."""

    def test_short_content_returns_all(self):
        content = "line1\nline2\nline3"
        preview = _create_content_preview(content, head_lines=5, tail_lines=5)
        assert "line1" in preview
        assert "line2" in preview
        assert "line3" in preview
        assert "truncated" not in preview

    def test_long_content_shows_head_and_tail(self):
        lines = [f"line{i}" for i in range(100)]
        content = "\n".join(lines)
        preview = _create_content_preview(content, head_lines=3, tail_lines=3)
        assert "line0" in preview
        assert "line1" in preview
        assert "line2" in preview
        assert "line97" in preview
        assert "line99" in preview
        assert "truncated" in preview

    def test_exact_boundary(self):
        """Content with exactly head+tail lines should not truncate."""
        lines = [f"line{i}" for i in range(10)]
        content = "\n".join(lines)
        preview = _create_content_preview(content, head_lines=5, tail_lines=5)
        assert "truncated" not in preview


class TestDetectExtension:
    """Tests for _detect_extension."""

    def test_json_object(self):
        assert _detect_extension('{"key": "value"}') == ".json"

    def test_json_array(self):
        assert _detect_extension('[1, 2, 3]') == ".json"

    def test_json_with_whitespace(self):
        assert _detect_extension("  \n  {\"k\": 1}") == ".json"

    def test_markdown_content(self):
        assert _detect_extension("# Title\nSome text") == ".md"

    def test_plain_text(self):
        assert _detect_extension("Just plain text") == ".md"


# ---------------------------------------------------------------------------
# Tests for middleware eviction logic
# ---------------------------------------------------------------------------


class TestEvictionDecision:
    """Tests for _process_large_message size threshold."""

    def test_small_message_not_evicted(self):
        backend = _make_backend()
        mw = LargeResultEvictionMiddleware(backend=backend, tool_token_limit_before_evict=100)
        msg = ToolMessage(content="small output", tool_call_id="call_1", name="Tool")
        processed, files_update = mw._process_large_message(msg)
        assert processed.content == "small output"
        assert files_update is None
        backend.write.assert_not_called()

    def test_large_message_is_evicted(self):
        backend = _make_backend()
        limit = 10  # 10 tokens = 40 chars
        mw = LargeResultEvictionMiddleware(backend=backend, tool_token_limit_before_evict=limit)
        # Create content larger than 40 chars
        large_content = "x" * (NUM_CHARS_PER_TOKEN * limit + 100)
        msg = ToolMessage(content=large_content, tool_call_id="call_1", name="Tool")
        processed, files_update = mw._process_large_message(msg)
        assert files_update is not None
        assert "saved in the filesystem" in processed.content
        backend.write.assert_called_once()

    def test_zero_limit_disables_eviction(self):
        backend = _make_backend()
        mw = LargeResultEvictionMiddleware(backend=backend, tool_token_limit_before_evict=0)
        msg = ToolMessage(content="anything", tool_call_id="call_1", name="Tool")
        processed, files_update = mw._process_large_message(msg)
        assert files_update is None

    def test_write_failure_returns_original(self):
        backend = _make_backend(write_error="disk full")
        limit = 10
        mw = LargeResultEvictionMiddleware(backend=backend, tool_token_limit_before_evict=limit)
        large_content = "x" * (NUM_CHARS_PER_TOKEN * limit + 100)
        msg = ToolMessage(content=large_content, tool_call_id="call_1", name="Tool")
        processed, files_update = mw._process_large_message(msg)
        assert files_update is None
        # Original content is returned since write failed
        assert processed.content == large_content


class TestWrapToolCall:
    """Tests for sync wrap_tool_call excluded-tool bypass."""

    def test_excluded_tool_bypasses_eviction(self):
        backend = _make_backend()
        mw = LargeResultEvictionMiddleware(backend=backend, tool_token_limit_before_evict=10)
        for tool_name in TOOLS_EXCLUDED_FROM_EVICTION:
            request = _make_tool_request(tool_name)
            large_msg = ToolMessage(content="x" * 10000, tool_call_id="call_1", name=tool_name)
            handler = MagicMock(return_value=large_msg)
            result = mw.wrap_tool_call(request, handler)
            # Should pass through without eviction
            assert result.content == "x" * 10000
            handler.assert_called_once_with(request)

    def test_non_excluded_tool_may_evict(self):
        backend = _make_backend()
        limit = 10
        mw = LargeResultEvictionMiddleware(backend=backend, tool_token_limit_before_evict=limit)
        request = _make_tool_request("CustomTool")
        large_content = "x" * (NUM_CHARS_PER_TOKEN * limit + 100)
        msg = ToolMessage(content=large_content, tool_call_id="call_1", name="CustomTool")
        handler = MagicMock(return_value=msg)
        result = mw.wrap_tool_call(request, handler)
        # Should be evicted (result is a Command)
        from langgraph.types import Command
        assert isinstance(result, Command)


class TestAwrapToolCall:
    """Tests for async awrap_tool_call."""

    @pytest.mark.asyncio
    async def test_excluded_tool_bypasses_eviction_async(self):
        backend = _make_backend()
        mw = LargeResultEvictionMiddleware(backend=backend, tool_token_limit_before_evict=10)
        request = _make_tool_request("Read")
        msg = ToolMessage(content="x" * 10000, tool_call_id="call_1", name="Read")
        handler = AsyncMock(return_value=msg)
        result = await mw.awrap_tool_call(request, handler)
        assert result.content == "x" * 10000

    @pytest.mark.asyncio
    async def test_async_eviction(self):
        backend = _make_backend()
        limit = 10
        mw = LargeResultEvictionMiddleware(backend=backend, tool_token_limit_before_evict=limit)
        request = _make_tool_request("CustomTool")
        large_content = "x" * (NUM_CHARS_PER_TOKEN * limit + 100)
        msg = ToolMessage(content=large_content, tool_call_id="call_1", name="CustomTool")
        handler = AsyncMock(return_value=msg)
        result = await mw.awrap_tool_call(request, handler)
        from langgraph.types import Command
        assert isinstance(result, Command)

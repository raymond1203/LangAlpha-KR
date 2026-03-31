"""Tests for src.config.langsmith — base64 stripping from traces."""

import base64
import os
from unittest.mock import patch

import pytest

import src.config.langsmith as lf
from src.config.langsmith import (
    strip_base64_from_trace,
    _format_size,
    _looks_like_base64,
    get_filtered_langsmith_tracer,
)


def _make_data_url(mime: str = "image/png", size_bytes: int = 5000) -> str:
    """Build a ``data:<mime>;base64,...`` string of approximately *size_bytes*."""
    return f"data:{mime};base64,{base64.b64encode(b'x' * size_bytes).decode()}"


def _make_raw_b64(size_bytes: int = 5000) -> str:
    """Return a raw base64 string (no data: prefix)."""
    return base64.b64encode(b"x" * size_bytes).decode()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestFormatSize:
    def test_megabytes(self):
        assert _format_size(2_000_000) == "2.0MB"

    def test_kilobytes(self):
        assert "KB" in _format_size(50_000)

    def test_bytes(self):
        assert _format_size(500) == "500B"


class TestLooksLikeBase64:
    def test_valid_base64(self):
        assert _looks_like_base64(_make_raw_b64(5_000))

    def test_short_string(self):
        assert not _looks_like_base64("abc")

    def test_normal_text(self):
        assert not _looks_like_base64("This is a normal sentence with spaces." * 20)


# ---------------------------------------------------------------------------
# strip_base64_from_trace
# ---------------------------------------------------------------------------

class TestStripBase64FromTrace:
    # -- Data URL strings (format 1) ----------------------------------------

    def test_image_data_url(self):
        block = {"type": "image_url", "image_url": {"url": _make_data_url("image/png", 10_000)}}
        result = strip_base64_from_trace(block)
        assert result["image_url"]["url"].startswith("[image/png:")

    def test_short_data_url_passes_through(self):
        short = "data:image/png;base64,iVBOR"
        assert strip_base64_from_trace(short) == short

    def test_data_url_below_threshold(self):
        url = _make_data_url("image/png", 500)
        assert strip_base64_from_trace(url) == url

    # -- Raw base64 strings (format 2) -------------------------------------

    def test_pdf_raw_base64(self):
        block = {"base64": _make_raw_b64(50_000), "mime_type": "application/pdf", "filename": "r.pdf"}
        result = strip_base64_from_trace(block)
        assert result["base64"].startswith("[application/pdf:")
        assert result["filename"] == "r.pdf"

    def test_raw_base64_uses_mime_type_hint(self):
        block = {"data": _make_raw_b64(5_000), "media_type": "image/jpeg"}
        result = strip_base64_from_trace(block)
        assert result["data"].startswith("[image/jpeg:")

    def test_raw_base64_falls_back_to_binary(self):
        result = strip_base64_from_trace({"payload": _make_raw_b64(5_000)})
        assert result["payload"].startswith("[binary:")

    def test_short_raw_base64_passes_through(self):
        block = {"base64": "c2hvcnQ=", "mime_type": "text/plain"}
        assert strip_base64_from_trace(block) == block

    # -- Anthropic native (also raw base64, caught by format 2) -------------

    def test_anthropic_native_image_block(self):
        block = {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": _make_raw_b64(10_000)},
        }
        result = strip_base64_from_trace(block)
        assert result["source"]["data"].startswith("[image/png:")
        assert result["source"]["type"] == "base64"

    def test_anthropic_native_nested_in_message(self):
        msg = {
            "content": [
                {"type": "text", "text": "Check this"},
                {"type": "image", "source": {"media_type": "image/jpeg", "data": _make_raw_b64(50_000)}},
            ]
        }
        result = strip_base64_from_trace(msg)
        assert result["content"][0] == {"type": "text", "text": "Check this"}
        assert "[image/jpeg:" in result["content"][1]["source"]["data"]

    # -- Mixed / nested -----------------------------------------------------

    def test_nested_input_state(self):
        state = {
            "messages": [{
                "content": [
                    {"type": "text", "text": "Analyze"},
                    {"type": "image_url", "image_url": {"url": _make_data_url("image/png", 100_000)}},
                ],
            }],
            "current_agent": "ptc",
        }
        result = strip_base64_from_trace(state)
        assert "[image/png:" in result["messages"][0]["content"][1]["image_url"]["url"]
        assert result["current_agent"] == "ptc"

    def test_mixed_text_and_image_list(self):
        msgs = [
            {"type": "text", "text": "Attached:"},
            {"type": "image_url", "image_url": {"url": _make_data_url("image/jpeg", 8_000)}},
        ]
        result = strip_base64_from_trace(msgs)
        assert result[0] == {"type": "text", "text": "Attached:"}
        assert "[image/jpeg:" in result[1]["image_url"]["url"]

    def test_list_of_base64_strings(self):
        result = strip_base64_from_trace([_make_data_url("image/webp", 3_000), "normal"])
        assert "[image/webp:" in result[0]
        assert result[1] == "normal"

    # -- Edge cases ---------------------------------------------------------

    def test_plain_text_unchanged(self):
        data = {"role": "user", "content": "What is AAPL's price?"}
        assert strip_base64_from_trace(data) == data

    def test_no_base64_passthrough(self):
        data = {"key": "value", "nested": {"a": 1}, "list": [1, "two"]}
        assert strip_base64_from_trace(data) == data

    def test_empty_dict(self):
        assert strip_base64_from_trace({}) == {}

    def test_none_passthrough(self):
        assert strip_base64_from_trace(None) is None

    def test_does_not_mutate_original(self):
        url = _make_data_url("image/png", 5_000)
        original = {"image_url": {"url": url}}
        copy = {"image_url": {"url": url}}
        strip_base64_from_trace(original)
        assert original == copy

    # -- Threshold env var --------------------------------------------------

    def test_custom_threshold_via_env(self):
        raw = _make_raw_b64(500)
        url = _make_data_url("image/png", 500)

        lf._min_bytes_cache = None
        with patch.dict(os.environ, {"LANGSMITH_TRACE_FILTER_MIN_BYTES": "100"}):
            assert strip_base64_from_trace(url).startswith("[image/png:")
            assert strip_base64_from_trace({"base64": raw})["base64"].startswith("[binary:")

        lf._min_bytes_cache = None
        with patch.dict(os.environ, {"LANGSMITH_TRACE_FILTER_MIN_BYTES": "10000"}):
            assert strip_base64_from_trace(url) == url
            assert strip_base64_from_trace({"base64": raw})["base64"] == raw


# ---------------------------------------------------------------------------
# get_filtered_langsmith_tracer
# ---------------------------------------------------------------------------

class TestGetFilteredLangsmithTracer:
    @pytest.fixture(autouse=True)
    def _reset_client_cache(self):
        lf._cached_client = None
        yield
        lf._cached_client = None

    def test_returns_none_when_tracing_disabled(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("LANGSMITH_TRACING", None)
            assert get_filtered_langsmith_tracer() is None

    def test_tracer_from_env_var(self):
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "true", "LANGSMITH_TRACE_FILTER": "true"}):
            assert get_filtered_langsmith_tracer() is not None

    def test_returns_none_when_filter_disabled(self):
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "true", "LANGSMITH_TRACE_FILTER": "false"}):
            assert get_filtered_langsmith_tracer() is None

    def test_reuses_client(self):
        with patch.dict(os.environ, {"LANGSMITH_TRACING": "true", "LANGSMITH_TRACE_FILTER": "true"}):
            t1 = get_filtered_langsmith_tracer()
            t2 = get_filtered_langsmith_tracer()
            assert t1.client is t2.client

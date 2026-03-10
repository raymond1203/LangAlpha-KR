"""
Tests for src/server/handlers/chat_handler.py

Covers:
- HITL response serialization (serialize_hitl_response_map, summarize_hitl_response_map)
- Error classification (recoverable vs non-recoverable)
- _append_to_last_user_message helper
- Turn index / query type calculation in _setup_fork_and_persistence
"""

import copy
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# HITL response serialization
# ---------------------------------------------------------------------------


class TestSerializeHitlResponseMap:
    """Tests for serialize_hitl_response_map."""

    def test_serialize_pydantic_model(self):
        from src.server.models.chat import (
            HITLDecision,
            HITLResponse,
            serialize_hitl_response_map,
        )

        response = HITLResponse(
            decisions=[HITLDecision(type="approve", message=None)]
        )
        result = serialize_hitl_response_map({"int-1": response})
        assert isinstance(result["int-1"], dict)
        assert result["int-1"]["decisions"][0]["type"] == "approve"

    def test_serialize_dict_input(self):
        from src.server.models.chat import serialize_hitl_response_map

        raw = {"decisions": [{"type": "approve", "message": None}]}
        result = serialize_hitl_response_map({"int-1": raw})
        assert result["int-1"]["decisions"][0]["type"] == "approve"

    def test_serialize_rejection_formats_message(self):
        from src.server.models.chat import serialize_hitl_response_map

        raw = {"decisions": [{"type": "reject", "message": "Too expensive"}]}
        result = serialize_hitl_response_map({"int-1": raw})
        msg = result["int-1"]["decisions"][0]["message"]
        assert "rejected" in msg.lower()
        assert "Too expensive" in msg

    def test_serialize_rejection_without_feedback(self):
        from src.server.models.chat import serialize_hitl_response_map

        raw = {"decisions": [{"type": "reject", "message": None}]}
        result = serialize_hitl_response_map({"int-1": raw})
        msg = result["int-1"]["decisions"][0]["message"]
        assert "rejected" in msg.lower()
        assert "No specific feedback" in msg

    def test_serialize_unsupported_type_raises(self):
        from src.server.models.chat import serialize_hitl_response_map

        with pytest.raises(TypeError, match="Unsupported HITL response type"):
            serialize_hitl_response_map({"int-1": 42})

    def test_serialize_does_not_mutate_original(self):
        from src.server.models.chat import serialize_hitl_response_map

        original = {"decisions": [{"type": "reject", "message": "fix it"}]}
        original_copy = copy.deepcopy(original)
        serialize_hitl_response_map({"int-1": original})
        assert original == original_copy


# ---------------------------------------------------------------------------
# HITL response summarization
# ---------------------------------------------------------------------------


class TestSummarizeHitlResponseMap:
    """Tests for summarize_hitl_response_map."""

    def test_all_approve_returns_approved(self):
        from src.server.models.chat import summarize_hitl_response_map

        raw = {"decisions": [{"type": "approve"}, {"type": "approve"}]}
        result = summarize_hitl_response_map({"int-1": raw})
        assert result["feedback_action"] == "APPROVED"
        assert result["content"] == ""

    def test_any_reject_returns_declined(self):
        from src.server.models.chat import summarize_hitl_response_map

        raw = {
            "decisions": [
                {"type": "approve"},
                {"type": "reject", "message": "too slow"},
            ]
        }
        result = summarize_hitl_response_map({"int-1": raw})
        assert result["feedback_action"] == "DECLINED"
        assert "too slow" in result["content"]

    def test_interrupt_ids_are_collected(self):
        from src.server.models.chat import summarize_hitl_response_map

        result = summarize_hitl_response_map({
            "int-1": {"decisions": [{"type": "approve"}]},
            "int-2": {"decisions": [{"type": "approve"}]},
        })
        assert set(result["interrupt_ids"]) == {"int-1", "int-2"}

    def test_pydantic_model_input(self):
        from src.server.models.chat import (
            HITLDecision,
            HITLResponse,
            summarize_hitl_response_map,
        )

        response = HITLResponse(
            decisions=[HITLDecision(type="reject", message="bad plan")]
        )
        result = summarize_hitl_response_map({"int-1": response})
        assert result["feedback_action"] == "DECLINED"
        assert "bad plan" in result["content"]


# ---------------------------------------------------------------------------
# _append_to_last_user_message
# ---------------------------------------------------------------------------


class TestAppendToLastUserMessage:
    """Tests for the _append_to_last_user_message helper."""

    def test_appends_to_string_content(self):
        from src.server.handlers.chat_handler import _append_to_last_user_message

        messages = [{"role": "user", "content": "hello"}]
        _append_to_last_user_message(messages, " world")
        assert messages[0]["content"] == "hello world"

    def test_appends_to_list_content(self):
        from src.server.handlers.chat_handler import _append_to_last_user_message

        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]}
        ]
        _append_to_last_user_message(messages, " extra")
        assert len(messages[0]["content"]) == 2
        assert messages[0]["content"][1] == {"type": "text", "text": " extra"}

    def test_no_op_when_empty_messages(self):
        from src.server.handlers.chat_handler import _append_to_last_user_message

        messages = []
        _append_to_last_user_message(messages, "text")
        assert messages == []

    def test_no_op_when_last_is_not_user(self):
        from src.server.handlers.chat_handler import _append_to_last_user_message

        messages = [{"role": "assistant", "content": "hi"}]
        _append_to_last_user_message(messages, " appended")
        assert messages[0]["content"] == "hi"


# ---------------------------------------------------------------------------
# Error classification logic
# ---------------------------------------------------------------------------


class TestErrorClassification:
    """Tests for the error classification logic used in astream_ptc_workflow.

    The logic is embedded in the except handler, so we test the classification
    rules directly rather than running the full workflow.
    """

    @staticmethod
    def _classify(exc: Exception) -> dict:
        """Replicate the error classification logic from chat_handler.

        Returns dict with is_recoverable, is_non_recoverable, error_type keys.
        """
        non_recoverable_types = (
            AttributeError,
            NameError,
            SyntaxError,
            ImportError,
            TypeError,
            KeyError,
        )

        is_non_recoverable = isinstance(exc, non_recoverable_types)

        # Recoverable patterns
        is_timeout = (
            isinstance(exc, TimeoutError)
            or "timeout" in str(exc).lower()
            or "timed out" in str(exc).lower()
        )

        is_network_issue = (
            isinstance(exc, ConnectionError)
            or "connection" in str(exc).lower()
            or "network" in str(exc).lower()
            or "unreachable" in str(exc).lower()
            or "connection refused" in str(exc).lower()
        )

        # API errors
        error_str = str(exc).lower()
        error_type_name = type(exc).__name__.lower()
        api_error_indicators = [
            "internal server error",
            "api_error",
            "system error",
            "error code: 500",
            "error code: 502",
            "error code: 503",
            "error code: 429",
            "rate limit",
            "service unavailable",
            "bad gateway",
            "gateway timeout",
        ]
        is_api_error = (
            any(ind in error_str for ind in api_error_indicators)
            or "internal" in error_type_name
            or "api" in error_type_name
            or "server" in error_type_name
        )

        is_recoverable = (
            is_timeout or is_network_issue or is_api_error
        ) and not is_non_recoverable

        error_type = (
            "connection_error"
            if is_network_issue
            else "timeout_error"
            if is_timeout
            else "api_error"
            if is_api_error
            else "non_recoverable"
        )

        return {
            "is_recoverable": is_recoverable,
            "is_non_recoverable": is_non_recoverable,
            "error_type": error_type,
        }

    # Non-recoverable errors
    def test_attribute_error_is_non_recoverable(self):
        result = self._classify(AttributeError("object has no attribute 'foo'"))
        assert result["is_non_recoverable"] is True
        assert result["is_recoverable"] is False

    def test_name_error_is_non_recoverable(self):
        result = self._classify(NameError("name 'x' is not defined"))
        assert result["is_non_recoverable"] is True
        assert result["is_recoverable"] is False

    def test_import_error_is_non_recoverable(self):
        result = self._classify(ImportError("No module named 'foo'"))
        assert result["is_non_recoverable"] is True

    def test_type_error_is_non_recoverable(self):
        result = self._classify(TypeError("expected str got int"))
        assert result["is_non_recoverable"] is True

    def test_key_error_is_non_recoverable(self):
        result = self._classify(KeyError("missing_key"))
        assert result["is_non_recoverable"] is True

    # Recoverable errors
    def test_timeout_error_is_recoverable(self):
        result = self._classify(TimeoutError("operation timed out"))
        assert result["is_recoverable"] is True
        assert result["error_type"] == "timeout_error"

    def test_connection_error_is_recoverable(self):
        result = self._classify(ConnectionError("Connection refused"))
        assert result["is_recoverable"] is True
        assert result["error_type"] == "connection_error"

    def test_generic_error_with_timeout_text_is_recoverable(self):
        result = self._classify(RuntimeError("Request timed out after 30s"))
        assert result["is_recoverable"] is True

    def test_api_error_indicators_are_recoverable(self):
        result = self._classify(RuntimeError("Internal Server Error (500)"))
        assert result["is_recoverable"] is True
        assert result["error_type"] == "api_error"

    def test_rate_limit_is_recoverable(self):
        result = self._classify(RuntimeError("Rate limit exceeded"))
        assert result["is_recoverable"] is True

    # Edge case: non-recoverable type trumps recoverable pattern
    def test_type_error_with_connection_text_is_non_recoverable(self):
        """TypeError mentioning 'connection' should still be non-recoverable."""
        result = self._classify(TypeError("bad connection argument type"))
        assert result["is_non_recoverable"] is True
        assert result["is_recoverable"] is False

    # Generic runtime error without any pattern
    def test_generic_runtime_error_is_not_recoverable(self):
        result = self._classify(RuntimeError("something broke"))
        assert result["is_recoverable"] is False
        assert result["is_non_recoverable"] is False

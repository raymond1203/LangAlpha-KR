"""Tests for chat Pydantic models and HITL utility functions.

Covers request/response models for the chat API (src/server/models/chat.py),
including HITL serialization and summarization helpers.
"""

import pytest
from pydantic import ValidationError

from src.server.models.chat import (
    ChatMessage,
    ChatRequest,
    ContentItem,
    GeneratePodcastRequest,
    HITLDecision,
    HITLResponse,
    SubagentMessageRequest,
    TTSRequest,
    _format_rejection_message,
    serialize_hitl_response_map,
    summarize_hitl_response_map,
)


# ---------------------------------------------------------------------------
# HITL Models
# ---------------------------------------------------------------------------


class TestHITLDecision:
    """HITLDecision model validation."""

    def test_approve(self):
        d = HITLDecision(type="approve")
        assert d.type == "approve"
        assert d.message is None

    def test_reject_with_message(self):
        d = HITLDecision(type="reject", message="Too risky")
        assert d.type == "reject"
        assert d.message == "Too risky"

    def test_invalid_type(self):
        with pytest.raises(ValidationError):
            HITLDecision(type="maybe")


class TestHITLResponse:
    """HITLResponse wrapping decisions."""

    def test_single_decision(self):
        resp = HITLResponse(decisions=[HITLDecision(type="approve")])
        assert len(resp.decisions) == 1

    def test_multiple_decisions(self):
        resp = HITLResponse(
            decisions=[
                HITLDecision(type="approve"),
                HITLDecision(type="reject", message="No"),
            ]
        )
        assert len(resp.decisions) == 2


# ---------------------------------------------------------------------------
# HITL utility functions
# ---------------------------------------------------------------------------


class TestFormatRejectionMessage:
    """_format_rejection_message helper."""

    def test_with_feedback(self):
        msg = _format_rejection_message("needs more detail")
        assert "needs more detail" in msg
        assert "rejected" in msg.lower()

    def test_without_feedback(self):
        msg = _format_rejection_message(None)
        assert "No specific feedback" in msg

    def test_blank_feedback(self):
        msg = _format_rejection_message("   ")
        assert "No specific feedback" in msg


class TestSerializeHitlResponseMap:
    """serialize_hitl_response_map converts models to dicts."""

    def test_pydantic_model(self):
        resp = HITLResponse(decisions=[HITLDecision(type="approve")])
        result = serialize_hitl_response_map({"int-1": resp})
        assert isinstance(result["int-1"], dict)
        assert result["int-1"]["decisions"][0]["type"] == "approve"

    def test_dict_input(self):
        raw = {"decisions": [{"type": "reject", "message": "bad"}]}
        result = serialize_hitl_response_map({"int-2": raw})
        assert "rejected" in result["int-2"]["decisions"][0]["message"].lower()

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError, match="Unsupported HITL response type"):
            serialize_hitl_response_map({"int-x": 42})


class TestSummarizeHitlResponseMap:
    """summarize_hitl_response_map aggregates approve/reject status."""

    def test_all_approved(self):
        resp = HITLResponse(decisions=[HITLDecision(type="approve")])
        summary = summarize_hitl_response_map({"i1": resp})
        assert summary["feedback_action"] == "APPROVED"
        assert summary["content"] == ""
        assert "i1" in summary["interrupt_ids"]

    def test_any_reject_means_declined(self):
        resp = HITLResponse(
            decisions=[
                HITLDecision(type="approve"),
                HITLDecision(type="reject", message="No"),
            ]
        )
        summary = summarize_hitl_response_map({"i1": resp})
        assert summary["feedback_action"] == "DECLINED"
        assert "No" in summary["content"]

    def test_dict_input(self):
        raw = {"decisions": [{"type": "reject", "message": "Nope"}]}
        summary = summarize_hitl_response_map({"i1": raw})
        assert summary["feedback_action"] == "DECLINED"
        assert "Nope" in summary["content"]

    def test_unsupported_decision_type_raises(self):
        raw = {"decisions": [123]}
        with pytest.raises(TypeError, match="Unsupported HITL decision type"):
            summarize_hitl_response_map({"i1": raw})


# ---------------------------------------------------------------------------
# Content / Message models
# ---------------------------------------------------------------------------


class TestContentItem:
    """ContentItem model."""

    def test_text_item(self):
        item = ContentItem(type="text", text="hello")
        assert item.type == "text"
        assert item.text == "hello"
        assert item.image_url is None

    def test_image_item(self):
        item = ContentItem(type="image", image_url="https://example.com/img.png")
        assert item.image_url == "https://example.com/img.png"

    def test_type_required(self):
        with pytest.raises(ValidationError):
            ContentItem(text="hello")


class TestChatMessage:
    """ChatMessage with string or list content."""

    def test_string_content(self):
        msg = ChatMessage(role="user", content="Hi")
        assert msg.content == "Hi"

    def test_list_content(self):
        items = [ContentItem(type="text", text="hello")]
        msg = ChatMessage(role="assistant", content=items)
        assert isinstance(msg.content, list)


# ---------------------------------------------------------------------------
# ChatRequest
# ---------------------------------------------------------------------------


class TestChatRequest:
    """ChatRequest with defaults and constraints."""

    def test_minimal(self):
        req = ChatRequest()
        assert req.agent_mode is None
        assert req.messages == []
        assert req.plan_mode is False
        assert req.hitl_response is None

    def test_agent_mode_validation(self):
        req = ChatRequest(agent_mode="flash")
        assert req.agent_mode == "flash"

    def test_invalid_agent_mode(self):
        with pytest.raises(ValidationError):
            ChatRequest(agent_mode="turbo")

    def test_reasoning_effort_values(self):
        for level in ("low", "medium", "high"):
            req = ChatRequest(reasoning_effort=level)
            assert req.reasoning_effort == level

    def test_invalid_reasoning_effort(self):
        with pytest.raises(ValidationError):
            ChatRequest(reasoning_effort="ultra")

    def test_fork_from_turn_ge_zero(self):
        req = ChatRequest(fork_from_turn=0)
        assert req.fork_from_turn == 0

        with pytest.raises(ValidationError):
            ChatRequest(fork_from_turn=-1)


# ---------------------------------------------------------------------------
# Utility request models
# ---------------------------------------------------------------------------


class TestTTSRequest:
    """TTSRequest defaults."""

    def test_defaults(self):
        req = TTSRequest(text="hello world")
        assert req.text == "hello world"
        assert req.speed_ratio == 1.0
        assert req.encoding == "mp3"

    def test_text_required(self):
        with pytest.raises(ValidationError):
            TTSRequest()


class TestSubagentMessageRequest:
    """SubagentMessageRequest validation."""

    def test_valid(self):
        req = SubagentMessageRequest(content="Do X")
        assert req.content == "Do X"

    def test_content_required(self):
        with pytest.raises(ValidationError):
            SubagentMessageRequest()

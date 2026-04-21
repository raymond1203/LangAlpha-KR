"""Tests for the TodoWrite tool's schema enforcement.

The tool's signature uses `List[TodoItem]`, so LangChain's argument parser
validates payloads via Pydantic before the function body runs. Invalid shapes
(stringified JSON, missing required fields, bad enum values) raise
ValidationError, which LangChain surfaces to the LLM as an error ToolMessage.
"""

import pytest
from pydantic import ValidationError

from ptc_agent.agent.tools.todo.tool import TodoWrite


def _good_todo(status: str = "in_progress") -> dict:
    return {
        "content": "Fetch Q3 earnings",
        "activeForm": "Fetching Q3 earnings",
        "status": status,
    }


class TestTodoWriteValidInput:
    def test_single_in_progress_todo(self):
        result = TodoWrite.invoke({"todos": [_good_todo("in_progress")]})
        assert "Todos have been modified successfully" in result

    def test_all_completed_returns_completion_message(self):
        todos = [_good_todo("completed"), _good_todo("completed")]
        result = TodoWrite.invoke({"todos": todos})
        assert "✓ All tasks completed" in result

    def test_one_remaining_reminder(self):
        todos = [
            _good_todo("completed"),
            _good_todo("completed"),
            _good_todo("in_progress"),
        ]
        result = TodoWrite.invoke({"todos": todos})
        assert "One task remaining" in result

    def test_empty_list_is_valid(self):
        result = TodoWrite.invoke({"todos": []})
        assert "Todos have been modified successfully" in result


class TestTodoWriteRejectsInvalidShapes:
    """These payloads should fail at the tool's pydantic arg parser."""

    def test_stringified_json_list_rejected(self):
        with pytest.raises(ValidationError):
            TodoWrite.invoke({"todos": '[{"status": "pending"}]'})

    def test_malformed_string_rejected(self):
        with pytest.raises(ValidationError):
            TodoWrite.invoke({"todos": "not a list at all"})

    def test_dict_instead_of_list_rejected(self):
        with pytest.raises(ValidationError):
            TodoWrite.invoke({"todos": {"status": "pending"}})

    def test_none_rejected(self):
        with pytest.raises(ValidationError):
            TodoWrite.invoke({"todos": None})

    def test_int_rejected(self):
        with pytest.raises(ValidationError):
            TodoWrite.invoke({"todos": 42})


class TestTodoWriteRejectsMalformedItems:
    def test_missing_content_rejected(self):
        bad = {"activeForm": "Running", "status": "pending"}
        with pytest.raises(ValidationError):
            TodoWrite.invoke({"todos": [bad]})

    def test_missing_activeForm_rejected(self):
        bad = {"content": "Run", "status": "pending"}
        with pytest.raises(ValidationError):
            TodoWrite.invoke({"todos": [bad]})

    def test_missing_status_rejected(self):
        bad = {"content": "Run", "activeForm": "Running"}
        with pytest.raises(ValidationError):
            TodoWrite.invoke({"todos": [bad]})

    def test_empty_content_rejected(self):
        bad = {"content": "", "activeForm": "Running", "status": "pending"}
        with pytest.raises(ValidationError):
            TodoWrite.invoke({"todos": [bad]})

    def test_empty_activeForm_rejected(self):
        bad = {"content": "Run", "activeForm": "", "status": "pending"}
        with pytest.raises(ValidationError):
            TodoWrite.invoke({"todos": [bad]})

    def test_invalid_status_value_rejected(self):
        bad = {"content": "Run", "activeForm": "Running", "status": "stale"}
        with pytest.raises(ValidationError):
            TodoWrite.invoke({"todos": [bad]})

    def test_item_must_be_dict_not_string(self):
        with pytest.raises(ValidationError):
            TodoWrite.invoke({"todos": ["just a string"]})


class TestTodoWriteValidationErrorMessageIsUsable:
    """LLM receives this as error ToolMessage — make sure the message names the problem."""

    def test_missing_field_error_names_field(self):
        with pytest.raises(ValidationError) as exc:
            TodoWrite.invoke({"todos": [{"content": "X", "status": "pending"}]})
        # Pydantic includes the missing field name in the error
        assert "activeForm" in str(exc.value)

    def test_bad_status_error_names_valid_options(self):
        bad = {"content": "X", "activeForm": "Xing", "status": "archived"}
        with pytest.raises(ValidationError) as exc:
            TodoWrite.invoke({"todos": [bad]})
        # Enum error mentions the invalid value or valid choices
        msg = str(exc.value).lower()
        assert "status" in msg

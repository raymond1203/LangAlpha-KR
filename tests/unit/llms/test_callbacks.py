# FORK: CostTrackingCallback 단위 테스트
"""Tests for src.llms.callbacks.CostTrackingCallback."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from src.llms.callbacks import CostTrackingCallback, init_cost_tracker


def _fake_response(input_tokens: int = 100, output_tokens: int = 30):
    """Build a minimal LLMResult-like object with usage_metadata."""

    class _Msg:
        usage_metadata = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }

    class _Gen:
        message = _Msg()

    class _Resp:
        generations = [[_Gen()]]
        llm_output = {"model_name": "claude-sonnet-4-6"}

    return _Resp()


class TestCostTrackingCallback:
    """기본 콜백 동작."""

    def test_records_basic_call_with_pricing(self, tmp_path, monkeypatch):
        """정상 호출 시 토큰/비용/지연이 기록된다."""
        monkeypatch.setenv("COST_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("COST_LOG_ENABLED", "true")
        cb = CostTrackingCallback(thread_id="abcd1234", default_tag="ptc")

        run_id = uuid4()
        with patch(
            "src.llms.callbacks.find_model_pricing",
            return_value={"input": 3.0, "output": 15.0, "unit": "per_1m_tokens"},
        ), patch(
            "src.llms.callbacks.detect_provider_for_model",
            return_value="anthropic",
        ):
            cb.on_chat_model_start(
                serialized={"name": "ChatAnthropic", "kwargs": {"model": "claude-sonnet-4-6"}},
                messages=[],
                run_id=run_id,
                metadata={"tag": "research", "billing_type": "byok"},
            )
            cb.on_llm_end(_fake_response(input_tokens=1000, output_tokens=200), run_id=run_id)

        assert len(cb.records) == 1
        rec = cb.records[0]
        assert rec["tag"] == "research"
        assert rec["billing_type"] == "byok"
        assert rec["model"] == "claude-sonnet-4-6"
        assert rec["input_tokens"] == 1000
        assert rec["output_tokens"] == 200
        assert rec["cost_usd"] > 0  # 1000 in × $3/M + 200 out × $15/M = $0.003 + $0.003
        assert rec["latency_ms"] >= 0

    def test_default_tag_when_metadata_missing(self, tmp_path, monkeypatch):
        """metadata 없이 호출돼도 default_tag 가 기록된다."""
        monkeypatch.setenv("COST_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("COST_LOG_ENABLED", "true")
        cb = CostTrackingCallback(thread_id="t1234", default_tag="flash")

        run_id = uuid4()
        with patch(
            "src.llms.callbacks.find_model_pricing", return_value=None
        ):
            cb.on_chat_model_start(
                serialized={"name": "ChatAnthropic"},
                messages=[],
                run_id=run_id,
                metadata=None,
            )
            cb.on_llm_end(_fake_response(), run_id=run_id)

        assert cb.records[0]["tag"] == "flash"

    def test_missing_pricing_does_not_crash(self, tmp_path, monkeypatch):
        """pricing 못 찾으면 cost=0 + error 로 기록되고 예외 없음."""
        monkeypatch.setenv("COST_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("COST_LOG_ENABLED", "true")
        cb = CostTrackingCallback(thread_id="x9999", default_tag="ptc")

        run_id = uuid4()
        with patch(
            "src.llms.callbacks.find_model_pricing", return_value=None
        ):
            cb.on_chat_model_start(
                serialized={"name": "ChatAnthropic"},
                messages=[],
                run_id=run_id,
                metadata={"tag": "ptc"},
            )
            cb.on_llm_end(_fake_response(), run_id=run_id)

        rec = cb.records[0]
        assert rec["cost_usd"] == 0.0
        assert rec["cost_error"] == "pricing_not_found"

    def test_jsonl_file_written(self, tmp_path, monkeypatch):
        """COST_LOG_ENABLED=true 면 JSONL 파일이 생성된다."""
        monkeypatch.setenv("COST_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("COST_LOG_ENABLED", "true")
        cb = CostTrackingCallback(thread_id="abcdef9876", default_tag="ptc")

        run_id = uuid4()
        with patch(
            "src.llms.callbacks.find_model_pricing", return_value=None
        ):
            cb.on_chat_model_start(
                serialized={"name": "ChatAnthropic"},
                messages=[],
                run_id=run_id,
                metadata={"tag": "ptc"},
            )
            cb.on_llm_end(_fake_response(), run_id=run_id)

        # tmp_path 하위에 YYYY-MM-DD/HHMM-9876.jsonl 파일 생성 확인
        files = list(Path(tmp_path).rglob("*.jsonl"))
        assert len(files) == 1
        line = files[0].read_text(encoding="utf-8").strip()
        rec = json.loads(line)
        assert rec["thread_id"] == "abcdef9876"
        assert rec["tag"] == "ptc"

    def test_log_disabled_does_not_write(self, tmp_path, monkeypatch):
        """COST_LOG_ENABLED=false 면 JSONL 파일이 생성되지 않는다."""
        monkeypatch.setenv("COST_LOG_DIR", str(tmp_path))
        monkeypatch.setenv("COST_LOG_ENABLED", "false")
        cb = CostTrackingCallback(thread_id="zz9999", default_tag="flash")

        run_id = uuid4()
        with patch(
            "src.llms.callbacks.find_model_pricing", return_value=None
        ):
            cb.on_chat_model_start(
                serialized={}, messages=[], run_id=run_id, metadata=None
            )
            cb.on_llm_end(_fake_response(), run_id=run_id)

        files = list(Path(tmp_path).rglob("*.jsonl"))
        assert files == []

    def test_on_llm_error_clears_run_state(self, tmp_path, monkeypatch):
        """LLM 에러 발생 시 run state 정리되고 records 에는 추가 안 됨."""
        monkeypatch.setenv("COST_LOG_DIR", str(tmp_path))
        cb = CostTrackingCallback(thread_id="errr0001", default_tag="ptc")

        run_id = uuid4()
        cb.on_chat_model_start(
            serialized={}, messages=[], run_id=run_id, metadata=None
        )
        assert run_id in cb._run_started_ns
        cb.on_llm_error(RuntimeError("boom"), run_id=run_id)
        assert run_id not in cb._run_started_ns
        assert cb.records == []

    def test_end_without_start_is_safe(self, monkeypatch, tmp_path):
        """on_llm_end 가 짝 없는 run_id 로 와도 예외 없이 무시된다."""
        monkeypatch.setenv("COST_LOG_DIR", str(tmp_path))
        cb = CostTrackingCallback(thread_id="orph0002", default_tag="ptc")
        cb.on_llm_end(_fake_response(), run_id=uuid4())
        assert cb.records == []


class TestInitCostTracker:
    """팩토리 헬퍼."""

    def test_returns_callback_instance(self):
        cb = init_cost_tracker(thread_id="th-xyz", default_tag="research")
        assert isinstance(cb, CostTrackingCallback)
        assert cb.thread_id == "th-xyz"
        assert cb.default_tag == "research"

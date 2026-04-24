# FORK: LLM 호출의 비용/지연을 구조화해 기록하는 LangChain 콜백
"""Cost and latency tracking callback for LLM calls.

이 콜백은 기존 ``PerCallTokenTracker`` 와 **병행** 사용된다. 역할 구분:

- ``PerCallTokenTracker`` (업스트림): per-call 토큰 기록 + billing_type 추적
- ``CostTrackingCallback`` (FORK): 각 호출의 **비용 / 지연 / 역할 태그**를 계산해서
  stdout + JSONL 파일로 적재, 나중에 CloudWatch 로 내보낼 수 있게 구조화
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import UUID

from langchain_core.callbacks.base import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from src.llms.pricing_utils import (
    calculate_total_cost,
    detect_provider_for_model,
    find_model_pricing,
)
from src.llms.token_counter import extract_token_usage

logger = logging.getLogger(__name__)


_ENV_LOG_DIR = "COST_LOG_DIR"
_ENV_LOG_ENABLED = "COST_LOG_ENABLED"
_ENV_CLOUDWATCH_ENABLED = "AWS_CLOUDWATCH_METRICS"
_DEFAULT_LOG_DIR = "./log/cost"


class CostTrackingCallback(BaseCallbackHandler):
    """각 LLM 호출의 비용/지연/역할을 기록하는 LangChain 콜백.

    한 워크플로우(thread)당 하나를 만들어 그래프 config 의 ``callbacks`` 리스트에
    태운다. ``PerCallTokenTracker`` 와 독립적으로 동작하므로 기존 토큰 추적
    파이프라인은 그대로 유지된다.

    기록 채널:
    - 항상: 구조화된 JSON 로그 라인 (logger.info)
    - ``COST_LOG_ENABLED=true`` (기본값): ``./log/cost/YYYY-MM-DD/HHMM-{last4}.jsonl``
      에 append. 파일명은 ``result_logger.py`` 와 동일하게 thread_id 의 마지막
      4자리를 사용 (짧은 윈도우 내 충돌 확률 낮음). ``COST_LOG_DIR`` 환경변수로
      디렉토리 오버라이드.
    - ``AWS_CLOUDWATCH_METRICS=true`` 시 ``boto3`` 로 custom metric 발행
      (현재는 디버그 로그만 찍는 stub; 실제 연결은 배포 인프라 구축 시 추가).

    태그 전파는 LLM 인스턴스의 ``metadata`` 를 통해 일어난다. 호출자가
    ``llm.bind(metadata={"tag": "flash"})`` 또는 ``chain.with_config(
    {"metadata": {"tag": "research"}})`` 로 지정하면 ``on_chat_model_start``
    에서 잡아낸다. metadata 가 없으면 ``tag="unknown"`` 으로 기록된다.
    """

    def __init__(
        self,
        thread_id: str,
        default_tag: str = "unknown",
        log_dir: Optional[str] = None,
        cloudwatch_namespace: str = "langalpha/llm",
    ) -> None:
        super().__init__()
        self.thread_id = thread_id
        self.default_tag = default_tag
        self.cloudwatch_namespace = cloudwatch_namespace

        self._lock = threading.Lock()
        # run_id → start timestamp (perf_counter ns)
        self._run_started_ns: Dict[UUID, int] = {}
        # run_id → metadata captured at start (tag, billing_type, model)
        self._run_metadata: Dict[UUID, Dict[str, Any]] = {}
        # 완료된 호출 기록 (테스트 및 디버깅에 유용)
        self.records: List[Dict[str, Any]] = []

        self.log_enabled = (
            os.getenv(_ENV_LOG_ENABLED, "true").lower() == "true"
        )
        resolved_dir = log_dir or os.getenv(_ENV_LOG_DIR, _DEFAULT_LOG_DIR)
        self.log_dir = Path(resolved_dir)
        self.cloudwatch_enabled = (
            os.getenv(_ENV_CLOUDWATCH_ENABLED, "false").lower() == "true"
        )

    # ------------------------------------------------------------------
    # LangChain 콜백 훅
    # ------------------------------------------------------------------

    def on_chat_model_start(
        self,
        serialized: Dict[str, Any],
        messages: List[Any],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        self._record_start(run_id, serialized, metadata)

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        # Chat/text LLM 모두 대응. chat 모델은 ``on_chat_model_start`` 가
        # 먼저 호출되므로 이 경로는 레거시 호출용.
        self._record_start(run_id, serialized, metadata)

    def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        try:
            self._record_end(run_id, response)
        except Exception as e:
            # 콜백 실패가 실제 쿼리를 막지 않도록 방어
            logger.warning(
                "CostTrackingCallback failed to record end for run_id=%s: %s",
                run_id,
                e,
            )
            with self._lock:
                self._run_started_ns.pop(run_id, None)
                self._run_metadata.pop(run_id, None)

    def on_llm_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: Optional[UUID] = None,
        **kwargs: Any,
    ) -> None:
        with self._lock:
            self._run_started_ns.pop(run_id, None)
            self._run_metadata.pop(run_id, None)

    # ------------------------------------------------------------------
    # 내부 기록 로직
    # ------------------------------------------------------------------

    def _record_start(
        self,
        run_id: UUID,
        serialized: Dict[str, Any],
        metadata: Optional[Dict[str, Any]],
    ) -> None:
        now_ns = time.perf_counter_ns()
        md: Dict[str, Any] = {
            "tag": self.default_tag,
            "billing_type": "platform",
            "model": None,
        }
        if metadata:
            if "tag" in metadata:
                md["tag"] = metadata["tag"]
            if "billing_type" in metadata:
                md["billing_type"] = metadata["billing_type"]
        # LangChain serialized 에서 model 이름 추출 시도
        if serialized:
            kwargs = serialized.get("kwargs") or {}
            model_name = (
                kwargs.get("model")
                or kwargs.get("model_name")
                or serialized.get("name")
            )
            if model_name:
                md["model"] = model_name
        with self._lock:
            self._run_started_ns[run_id] = now_ns
            self._run_metadata[run_id] = md

    def _record_end(self, run_id: UUID, response: LLMResult) -> None:
        end_ns = time.perf_counter_ns()
        with self._lock:
            start_ns = self._run_started_ns.pop(run_id, None)
            md = self._run_metadata.pop(run_id, None) or {}
        if start_ns is None:
            logger.debug(
                "CostTrackingCallback: end without start (run_id=%s)", run_id
            )
            return

        latency_ms = (end_ns - start_ns) / 1_000_000.0

        token_usage = _safe_extract_tokens(response)
        model_name = (
            token_usage.get("model_name")
            or md.get("model")
            or _extract_model_from_response(response)
            or "unknown"
        )

        cost_info = _compute_cost(
            model_name=model_name,
            billing_type=md.get("billing_type", "platform"),
            token_usage=token_usage,
        )

        record = {
            "thread_id": self.thread_id,
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "run_id": str(run_id),
            "tag": md.get("tag", self.default_tag),
            "model": model_name,
            "billing_type": md.get("billing_type", "platform"),
            "input_tokens": token_usage.get("input_tokens", 0),
            "output_tokens": token_usage.get("output_tokens", 0),
            "total_tokens": token_usage.get("total_tokens", 0),
            "cost_usd": cost_info.get("total_cost", 0.0),
            "cost_error": cost_info.get("error"),
            "latency_ms": round(latency_ms, 2),
        }
        self.records.append(record)

        logger.info("llm_cost %s", json.dumps(record, ensure_ascii=False))

        if self.log_enabled:
            self._append_jsonl(record)

        if self.cloudwatch_enabled:
            self._emit_cloudwatch(record)

    # ------------------------------------------------------------------
    # 출력 채널
    # ------------------------------------------------------------------

    def _append_jsonl(self, record: Dict[str, Any]) -> None:
        now = datetime.now(timezone.utc)
        day_dir = self.log_dir / now.strftime("%Y-%m-%d")
        try:
            day_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            logger.warning(
                "CostTrackingCallback: cannot create log dir %s: %s", day_dir, e
            )
            return

        last4 = self.thread_id[-4:] if len(self.thread_id) >= 4 else self.thread_id
        filename = f"{now.strftime('%H%M')}-{last4}.jsonl"
        path = day_dir / filename
        try:
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(
                "CostTrackingCallback: cannot write %s: %s", path, e
            )

    def _emit_cloudwatch(self, record: Dict[str, Any]) -> None:
        """CloudWatch custom metric 발행 훅. 현재는 디버그 로그만 찍는 stub."""
        logger.debug(
            "cloudwatch_stub namespace=%s record=%s",
            self.cloudwatch_namespace,
            record,
        )


# ----------------------------------------------------------------------
# 헬퍼: 토큰 추출 / 비용 계산 (방어적 래퍼)
# ----------------------------------------------------------------------


def _safe_extract_tokens(response: Any) -> Dict[str, Any]:
    """``extract_token_usage`` 가 예외를 던져도 빈 dict 를 반환한다."""
    try:
        generations = getattr(response, "generations", None)
        if generations:
            for gen_list in generations:
                for gen in gen_list:
                    message = getattr(gen, "message", None)
                    if message is not None:
                        return extract_token_usage(message)
        return extract_token_usage(response)
    except Exception as e:
        logger.debug("extract_token_usage failed: %s", e)
        return {}


def _extract_model_from_response(response: Any) -> Optional[str]:
    llm_output = getattr(response, "llm_output", None)
    if isinstance(llm_output, dict):
        return llm_output.get("model_name") or llm_output.get("model")
    return None


def _compute_cost(
    model_name: str,
    billing_type: str,
    token_usage: Dict[str, Any],
) -> Dict[str, Any]:
    """pricing_utils 로 비용 계산. 실패 시 ``{'total_cost': 0.0, 'error': ...}``."""
    try:
        provider = detect_provider_for_model(model_name, billing_type=billing_type)
        pricing = find_model_pricing(model_name, provider=provider)
        if not pricing:
            return {"total_cost": 0.0, "error": "pricing_not_found"}

        input_tokens = token_usage.get("input_tokens", 0) or 0
        output_tokens = token_usage.get("output_tokens", 0) or 0
        cached = token_usage.get("cached_tokens", 0) or 0
        cache_5m = token_usage.get("cache_5m_tokens", 0) or 0
        cache_1h = token_usage.get("cache_1h_tokens", 0) or 0

        return calculate_total_cost(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached,
            cache_5m_tokens=cache_5m,
            cache_1h_tokens=cache_1h,
            pricing=pricing,
        )
    except Exception as e:
        logger.debug("cost calculation failed for %s: %s", model_name, e)
        return {"total_cost": 0.0, "error": str(e)}


# ----------------------------------------------------------------------
# 팩토리 헬퍼 (call site 에서 한 줄로 생성)
# ----------------------------------------------------------------------


def init_cost_tracker(
    thread_id: str,
    default_tag: str = "unknown",
) -> CostTrackingCallback:
    """표준 진입점. 워크플로우 핸들러가 thread 시작 시 한 번 호출."""
    return CostTrackingCallback(thread_id=thread_id, default_tag=default_tag)


__all__ = [
    "CostTrackingCallback",
    "init_cost_tracker",
]

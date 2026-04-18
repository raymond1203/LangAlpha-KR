"""Sanitize malformed Anthropic thinking blocks before they reach the API.

On Claude Opus 4.7 with adaptive thinking + default `display: "omitted"`,
the Anthropic stream only emits `signature_delta` events (no `thinking_delta`).
langchain-anthropic's stream handler turns each delta into a standalone
content block: `{"type": "thinking", "signature": "...", "index": N}` — with
no `thinking` field. The merged AIMessage lands in LangGraph state that way,
and the next turn replaying it to Anthropic fails with:

    messages.<i>.content.<j>.thinking.thinking: Field required

Anthropic's contract is that `type="thinking"` blocks must always carry a
`thinking` field, even when omitted (empty string is valid). This middleware
walks every AIMessage in the outgoing request and injects `thinking: ""` on
blocks that have a signature but are missing the `thinking` key. Signature
continuity is preserved; the schema is satisfied.
"""

from collections.abc import Awaitable, Callable
from copy import copy
import logging
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, AnyMessage

logger = logging.getLogger(__name__)


def _sanitize_thinking_block(block: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Return (block, changed). Injects thinking='' on orphan signature-only blocks."""
    if block.get("type") != "thinking":
        return block, False
    thinking = block.get("thinking")
    if isinstance(thinking, str):
        return block, False
    repaired = {**block, "thinking": ""}
    return repaired, True


def _sanitize_message(msg: AnyMessage) -> tuple[AnyMessage, int]:
    """Return (maybe-new message, count of blocks repaired)."""
    if not isinstance(msg, AIMessage):
        return msg, 0
    content = msg.content
    if not isinstance(content, list):
        return msg, 0

    new_blocks: list[Any] = []
    repaired_count = 0
    changed = False

    for block in content:
        if isinstance(block, dict):
            new_block, block_changed = _sanitize_thinking_block(block)
            if block_changed:
                repaired_count += 1
                changed = True
            new_blocks.append(new_block)
        else:
            new_blocks.append(block)

    if not changed:
        return msg, 0

    new_msg = copy(msg)
    new_msg.content = new_blocks
    return new_msg, repaired_count


def _sanitize_messages(messages: list[AnyMessage]) -> tuple[list[AnyMessage], int]:
    """Return (messages, total_blocks_repaired). Same list object if nothing changed."""
    result: list[AnyMessage] = []
    total = 0
    changed = False
    for msg in messages:
        new_msg, count = _sanitize_message(msg)
        total += count
        if new_msg is not msg:
            changed = True
        result.append(new_msg)
    return (result, total) if changed else (messages, 0)


class AnthropicThinkingSanitizerMiddleware(AgentMiddleware):
    """Repair orphan thinking blocks (missing `thinking` text) before the LLM call.

    See module docstring for root cause. This middleware should run innermost so
    it catches blocks introduced by any upstream middleware and it is the last
    thing to touch messages before the API call.
    """

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        sanitized, repaired = _sanitize_messages(request.messages)
        if repaired:
            logger.warning(
                "[ThinkingSanitizer] Repaired %d orphan thinking block(s) "
                "(injected thinking='') before Anthropic call",
                repaired,
            )
            request = request.override(messages=sanitized)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        sanitized, repaired = _sanitize_messages(request.messages)
        if repaired:
            logger.warning(
                "[ThinkingSanitizer] Repaired %d orphan thinking block(s) "
                "(injected thinking='') before Anthropic call",
                repaired,
            )
            request = request.override(messages=sanitized)
        return await handler(request)

"""Utility functions for secretary tools."""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 8000


def _parse_sse_string(raw: str) -> tuple[str, dict] | None:
    """Parse a raw SSE string into (event_type, data_dict).

    Raw SSE format: "id: 42\\nevent: message_chunk\\ndata: {...}\\n\\n"

    Args:
        raw: Raw SSE string from Redis

    Returns:
        Tuple of (event_type, data_dict) or None if parsing fails
    """
    try:
        event_type = ""
        data_str = ""

        for line in raw.split("\n"):
            line = line.strip()
            if line.startswith("event:"):
                event_type = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_str = line[len("data:"):].strip()

        if not event_type or not data_str:
            return None

        data = json.loads(data_str)
        return (event_type, data)
    except (json.JSONDecodeError, ValueError, AttributeError):
        return None


async def extract_text_from_thread(thread_id: str) -> dict[str, Any]:
    """Extract text content from a thread's SSE events.

    Reads from Redis if the thread is actively running, otherwise reads
    from the database. Filters for message_chunk events with text content.

    Args:
        thread_id: The conversation thread ID

    Returns:
        Dict with keys: text, status, thread_id, workspace_id
    """
    from src.server.database.conversation import (
        get_thread_by_id,
    )
    from src.server.services.workflow_tracker import WorkflowTracker

    # Look up thread
    thread = await get_thread_by_id(thread_id)
    if not thread:
        return {
            "text": "",
            "status": "not_found",
            "thread_id": thread_id,
            "workspace_id": "",
        }

    workspace_id = str(thread.get("workspace_id", ""))

    # Check workflow status
    tracker = WorkflowTracker.get_instance()
    status_info = await tracker.get_status(thread_id)

    if status_info:
        status = status_info.get("status", "unknown")
    else:
        status = thread.get("current_status", "unknown")

    # Determine if running (read from Redis) or completed (read from DB)
    active_statuses = {"running", "active", "streaming", "pending"}
    if status in active_statuses:
        text = await _extract_from_redis(thread_id)
    else:
        text = await _extract_from_db(thread_id)

    # Truncate if needed
    if len(text) > MAX_OUTPUT_CHARS:
        text = text[:MAX_OUTPUT_CHARS] + (
            "\n\n[truncated — full output available in workspace]"
        )

    return {
        "text": text,
        "status": status,
        "thread_id": thread_id,
        "workspace_id": workspace_id,
    }


async def _extract_from_redis(thread_id: str) -> str:
    """Extract text content from Redis SSE event buffer.

    Args:
        thread_id: The conversation thread ID

    Returns:
        Concatenated text content from message_chunk events
    """
    from src.utils.cache.redis_cache import get_cache_client

    try:
        cache = get_cache_client()
        raw_events = await cache.list_range(
            f"workflow:events:{thread_id}", start=-500, end=-1
        )
    except Exception as e:
        logger.error(f"Failed to read Redis events for thread {thread_id}: {e}")
        return ""

    chunks: list[str] = []
    for raw in raw_events:
        parsed = _parse_sse_string(raw)
        if parsed is None:
            continue
        event_type, data = parsed
        if (
            event_type == "message_chunk"
            and isinstance(data, dict)
            and data.get("content_type") == "text"
        ):
            content = data.get("content", "")
            if content:
                chunks.append(content)

    return "".join(chunks)


async def _extract_from_db(thread_id: str) -> str:
    """Extract text content from DB-persisted SSE events.

    Args:
        thread_id: The conversation thread ID

    Returns:
        Concatenated text content from message_chunk events
    """
    from src.server.database.conversation import get_responses_for_thread

    try:
        responses, _ = await get_responses_for_thread(thread_id)
    except Exception as e:
        logger.error(f"Failed to read DB responses for thread {thread_id}: {e}")
        return ""

    chunks: list[str] = []
    for response in responses:
        sse_events = response.get("sse_events")
        if not sse_events:
            continue
        for event in sse_events:
            if not isinstance(event, dict):
                continue
            if event.get("event") != "message_chunk":
                continue
            data = event.get("data", {})
            if not isinstance(data, dict):
                continue
            if data.get("content_type") == "text":
                content = data.get("content", "")
                if content:
                    chunks.append(content)

    return "".join(chunks)

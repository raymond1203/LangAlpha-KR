"""Redis key builders for memo metadata coordination.

Lives in the agent layer so ``metadata.py`` (agent) and ``memo.py`` (server)
can both import without crossing the agent → server boundary.
"""

from __future__ import annotations

from urllib.parse import quote


def _segment(value: str) -> str:
    """URL-encode a key component so embedded ``:`` cannot collide neighbours.

    Without this, ``user_id="a"``/``key="b:c"`` and
    ``user_id="a:b"``/``key="c"`` would map to the same Redis key.
    """
    return quote(value, safe="")


def memo_metadata_inflight_key(user_id: str, key: str) -> str:
    """Redis key advertising that some worker is generating metadata for this memo."""
    return f"memo:metadata:inflight:{_segment(user_id)}:{_segment(key)}"


def memo_metadata_cancel_key(user_id: str, key: str) -> str:
    """Redis key carrying a cooperative cross-worker cancel signal for this memo."""
    return f"memo:metadata:cancel:{_segment(user_id)}:{_segment(key)}"

"""Shared envelope helpers for OHLCV cache services (daily + intraday).

Provides the envelope structure, parsing, delta-merge, and SWR staleness
check used by both DailyCacheService and IntradayCacheService.
"""

import time
from bisect import bisect_left
from typing import Any, Dict, List, Optional

from src.utils.market_hours import is_market_closed

ENVELOPE_VERSION = 1
_SOFT_TTL_RATIO = 0.5  # refresh when 50% of TTL has elapsed
_EMPTY_RESULT_TTL = 30  # short TTL for empty upstream results


def _build_envelope(
    bars: List[Dict[str, Any]],
    market_phase: str,
    complete: bool,
    stored_ttl: int = 0,
) -> Dict[str, Any]:
    watermark = bars[-1]["date"] if bars else ""
    return {
        "v": ENVELOPE_VERSION,
        "bars": bars,
        "watermark": watermark,
        "fetched_at": time.time(),
        "market_phase": market_phase,
        "complete": complete,
        "stored_ttl": stored_ttl,
    }


def _parse_envelope(raw: Any) -> Optional[Dict[str, Any]]:
    """Return the envelope dict if valid, else None (treat as cache miss)."""
    if not isinstance(raw, dict):
        return None
    if raw.get("v") != ENVELOPE_VERSION:
        return None
    if "bars" not in raw:
        return None
    return raw


def _merge_bars(
    existing: List[Dict[str, Any]],
    delta: List[Dict[str, Any]],
    watermark: str,
) -> List[Dict[str, Any]]:
    """Merge delta bars into existing, keeping the immutable prefix intact.

    Everything before the watermark is immutable history.
    Delta replaces everything from the watermark onward.
    """
    if not existing:
        return delta
    if not delta:
        return existing

    # Find split point via bisect on the "date" field
    dates = [b["date"] for b in existing]
    split_idx = bisect_left(dates, watermark)
    return existing[:split_idx] + delta


def _needs_refresh(envelope: Dict[str, Any], ttl: int) -> bool:
    """Determine whether an SWR background refresh should fire."""
    if envelope.get("complete"):
        # Market is closed and all bars immutable — check transition
        if not is_market_closed():
            # Market has reopened since we set complete=True → force refresh
            return True
        return False

    elapsed = time.time() - envelope.get("fetched_at", 0)
    soft_threshold = ttl * _SOFT_TTL_RATIO
    return elapsed > soft_threshold

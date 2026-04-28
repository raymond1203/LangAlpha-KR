"""Tests for the centralized memo timestamp helper."""

from __future__ import annotations

import re

from ptc_agent.agent.memo._time import now_iso


_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")


def test_microsecond_format():
    assert _PATTERN.match(now_iso())


def test_two_calls_produce_distinct_timestamps_within_one_second():
    """The whole point of microsecond precision: defeat same-second CAS collisions."""
    a = now_iso()
    b = now_iso()
    # On any reasonable machine these calls are microseconds apart, never equal.
    assert a != b
    # Strictly monotonic — `b` was emitted after `a` so it must lex-sort after.
    # Pinning ordering (rather than the weaker "same second OR a<b") catches
    # regressions to a clock that wraps or jitters backwards mid-test.
    assert a < b

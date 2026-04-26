"""Unit tests for `get_locale_config` locale → timezone mapping.

Covers fork addition `ko-KR → Asia/Seoul` plus regression on existing locales.
"""

from __future__ import annotations

import pytest

from src.config.settings import get_locale_config


@pytest.mark.parametrize(
    "locale,expected_tz",
    [
        ("en-US", "America/New_York"),
        ("zh-CN", "Asia/Shanghai"),
        ("ko-KR", "Asia/Seoul"),
        # Case-insensitive
        ("EN-US", "America/New_York"),
        ("ko-kr", "Asia/Seoul"),
        ("KO-KR", "Asia/Seoul"),
    ],
)
def test_known_locales_map_to_timezone(locale: str, expected_tz: str) -> None:
    cfg = get_locale_config(locale, "en")
    assert cfg["timezone"] == expected_tz
    assert cfg["locale"] == locale
    assert cfg["prompt_language"] == "en"
    assert "timezone_label" in cfg


@pytest.mark.parametrize(
    "locale",
    ["fr-FR", "de-DE", "ja-JP", "", "unknown"],
)
def test_unknown_locale_falls_back_to_utc(locale: str) -> None:
    cfg = get_locale_config(locale, "en")
    assert cfg["timezone"] == "UTC"


def test_korean_locale_returns_asia_seoul_consistently() -> None:
    """Regression guard: ensure ko-KR doesn't accidentally fall through to UTC."""
    cfg = get_locale_config("ko-KR", "ko")
    assert cfg["timezone"] == "Asia/Seoul"
    assert cfg["prompt_language"] == "ko"

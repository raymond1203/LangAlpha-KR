"""Unit tests for scripts/ci/generate_release_notes.py."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add scripts/ci to path so we can import the module
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "ci"))
from generate_release_notes import format_notes, parse_commits


class TestParseCommits:
    """Test parse_commits with various conventional commit formats."""

    def test_conventional_types(self):
        """All conventional commit types are parsed into correct categories."""
        commits = [
            ("a" * 40, "feat: add login page"),
            ("b" * 40, "fix: resolve null pointer"),
            ("c" * 40, "perf: optimize query"),
            ("d" * 40, "refactor: extract helper"),
            ("e" * 40, "docs: update readme"),
            ("f" * 40, "chore: bump deps"),
        ]
        parsed = parse_commits(commits)
        assert "New Features" in parsed
        assert "Bug Fixes" in parsed
        assert "Performance" in parsed
        assert "Improvements" in parsed
        assert "Documentation" in parsed
        assert "Maintenance" in parsed

    def test_scoped_commits(self):
        """Scoped commits like feat(auth): msg produce scope='auth'."""
        commits = [("a" * 40, "feat(auth): add OAuth support")]
        parsed = parse_commits(commits)
        entry = parsed["New Features"][0]
        assert entry["scope"] == "auth"
        assert entry["message"] == "add OAuth support"

    def test_unscoped_commits(self):
        """Unscoped commits like feat: msg have scope=None."""
        commits = [("a" * 40, "feat: add dark mode")]
        parsed = parse_commits(commits)
        entry = parsed["New Features"][0]
        assert entry["scope"] is None
        assert entry["message"] == "add dark mode"

    def test_non_conventional_commits(self):
        """Non-conventional commits go to 'Other Changes'."""
        commits = [("a" * 40, "Update dependencies")]
        parsed = parse_commits(commits)
        assert "Other Changes" in parsed
        assert parsed["Other Changes"][0]["message"] == "Update dependencies"

    def test_empty_commit_list(self):
        """Empty commit list produces empty dict."""
        parsed = parse_commits([])
        assert parsed == {}

    def test_sha_truncation(self):
        """SHAs are truncated to 7 characters."""
        full_sha = "abcdef1234567890abcdef1234567890abcdef12"
        commits = [(full_sha, "feat: something")]
        parsed = parse_commits(commits)
        assert parsed["New Features"][0]["sha"] == "abcdef1"

    def test_extra_colons_in_message(self):
        """Commit messages with extra colons split on first colon only."""
        commits = [("a" * 40, "feat: foo: bar baz")]
        parsed = parse_commits(commits)
        entry = parsed["New Features"][0]
        assert entry["message"] == "foo: bar baz"

    def test_feature_alias(self):
        """'feature:' is an alias for 'feat:'."""
        commits = [("a" * 40, "feature(ui): new button")]
        parsed = parse_commits(commits)
        assert "New Features" in parsed
        assert parsed["New Features"][0]["scope"] == "ui"


class TestFormatNotes:
    """Test format_notes markdown rendering."""

    def test_empty_parsed_produces_no_changes(self):
        """Empty parsed dict produces 'No changes since last release'."""
        result = format_notes({}, "v1.0.0")
        assert "No changes since last release" in result
        assert "# v1.0.0" in result

    def test_category_display_order(self):
        """Categories appear in the defined display order."""
        parsed = {
            "Bug Fixes": [{"scope": None, "message": "fix it", "sha": "abc1234"}],
            "New Features": [
                {"scope": None, "message": "add it", "sha": "def5678"}
            ],
            "Other Changes": [
                {"scope": None, "message": "misc", "sha": "ghi9012"}
            ],
        }
        result = format_notes(parsed, "v1.0.0")
        feat_pos = result.index("## New Features")
        fix_pos = result.index("## Bug Fixes")
        other_pos = result.index("## Other Changes")
        assert feat_pos < fix_pos < other_pos

    def test_scoped_entry_formatting(self):
        """Scoped entries render as **scope:** message (sha)."""
        parsed = {
            "New Features": [
                {"scope": "auth", "message": "add login", "sha": "abc1234"}
            ]
        }
        result = format_notes(parsed, "v1.0.0")
        assert "- **auth:** add login (abc1234)" in result

    def test_unscoped_entry_formatting(self):
        """Unscoped entries render as message (sha) without bold prefix."""
        parsed = {
            "New Features": [
                {"scope": None, "message": "add dark mode", "sha": "abc1234"}
            ]
        }
        result = format_notes(parsed, "v1.0.0")
        assert "- add dark mode (abc1234)" in result

    def test_footer_counts(self):
        """Footer shows per-category counts and total."""
        parsed = {
            "New Features": [
                {"scope": None, "message": "a", "sha": "1234567"},
                {"scope": None, "message": "b", "sha": "2345678"},
            ],
            "Bug Fixes": [
                {"scope": None, "message": "c", "sha": "3456789"},
            ],
        }
        result = format_notes(parsed, "v1.0.0")
        assert "3 commits total" in result
        assert "2 features" in result
        assert "1 fix" in result

    def test_footer_pluralization(self):
        """Footer pluralizes correctly (fixes not fixs)."""
        parsed = {
            "Bug Fixes": [
                {"scope": None, "message": "a", "sha": "1234567"},
                {"scope": None, "message": "b", "sha": "2345678"},
            ],
        }
        result = format_notes(parsed, "v1.0.0")
        assert "2 fixes" in result
        assert "fixs" not in result

    def test_version_header(self):
        """Version string appears as H1 header."""
        parsed = {
            "New Features": [
                {"scope": None, "message": "a", "sha": "1234567"}
            ]
        }
        result = format_notes(parsed, "v2026.04.02")
        assert result.startswith("# v2026.04.02\n")


class TestOutputFile:
    """Test file output mode."""

    def test_output_to_file(self, tmp_path):
        """format_notes output can be written to a file."""
        parsed = {
            "New Features": [
                {"scope": None, "message": "a", "sha": "1234567"}
            ]
        }
        content = format_notes(parsed, "v1.0.0")
        out_file = tmp_path / "notes.md"
        out_file.write_text(content)
        assert out_file.read_text() == content
        assert "# v1.0.0" in out_file.read_text()

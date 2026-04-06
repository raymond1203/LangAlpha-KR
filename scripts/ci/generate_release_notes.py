#!/usr/bin/env python3
"""Generate structured release notes from conventional commits.

Usage:
    python3 scripts/ci/generate_release_notes.py --version v2026.04.02
    python3 scripts/ci/generate_release_notes.py --range v2026.04.01..HEAD --version v2026.04.02
    python3 scripts/ci/generate_release_notes.py --range v2026.04.01..HEAD --version v2026.04.02 --output release_notes.md
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

# Ordered: first match wins display position
CATEGORIES = [
    ("feat", "New Features"),
    ("feature", "New Features"),
    ("fix", "Bug Fixes"),
    ("perf", "Performance"),
    ("refactor", "Improvements"),
    ("docs", "Documentation"),
    ("chore", "Maintenance"),
]

# For the footer summary: (singular, plural)
CATEGORY_LABELS = {
    "New Features": ("feature", "features"),
    "Bug Fixes": ("fix", "fixes"),
    "Performance": ("performance improvement", "performance improvements"),
    "Improvements": ("improvement", "improvements"),
    "Documentation": ("doc change", "doc changes"),
    "Maintenance": ("maintenance change", "maintenance changes"),
    "Other Changes": ("other change", "other changes"),
}

# Pattern: type(scope): message  OR  type: message
CONVENTIONAL_RE = re.compile(
    r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?:\s*(?P<message>.+)$"
)


def get_commits(commit_range: str | None) -> list[tuple[str, str]]:
    """Run git log and return (full_sha, subject) pairs."""
    cmd = ["git", "log", "--no-merges", "--format=%H %s"]
    if commit_range:
        cmd.append(commit_range)
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    commits = []
    for line in result.stdout.strip().splitlines():
        if not line.strip():
            continue
        sha, subject = line.split(" ", 1)
        commits.append((sha, subject))
    return commits


def parse_commits(
    commits: list[tuple[str, str]],
) -> dict[str, list[dict[str, str]]]:
    """Parse commits into categorized groups.

    Returns dict mapping category name to list of
    {"scope": str|None, "message": str, "sha": str}.
    """
    groups: dict[str, list[dict[str, str]]] = {}

    for sha, subject in commits:
        short_sha = sha[:7]
        match = CONVENTIONAL_RE.match(subject)

        if match:
            commit_type = match.group("type")
            scope = match.group("scope")
            message = match.group("message")

            category = None
            for prefix, cat_name in CATEGORIES:
                if commit_type == prefix:
                    category = cat_name
                    break

            if category is None:
                category = "Other Changes"
        else:
            category = "Other Changes"
            scope = None
            message = subject

        if category not in groups:
            groups[category] = []
        groups[category].append(
            {"scope": scope, "message": message, "sha": short_sha}
        )

    return groups


def format_notes(parsed: dict[str, list[dict[str, str]]], version: str) -> str:
    """Render parsed commits as markdown release notes."""
    if not parsed:
        return f"# {version}\n\nNo changes since last release.\n"

    lines = [f"# {version}", ""]

    # Ordered output: follow CATEGORIES order, then "Other Changes" last
    seen_categories: list[str] = []
    for _, cat_name in CATEGORIES:
        if cat_name in parsed and cat_name not in seen_categories:
            seen_categories.append(cat_name)
    if "Other Changes" in parsed:
        seen_categories.append("Other Changes")

    for category in seen_categories:
        entries = parsed[category]
        lines.append(f"## {category}")
        for entry in entries:
            if entry["scope"]:
                lines.append(
                    f"- **{entry['scope']}:** {entry['message']} ({entry['sha']})"
                )
            else:
                lines.append(f"- {entry['message']} ({entry['sha']})")
        lines.append("")

    # Footer with counts
    lines.append("---")
    count_parts = []
    total = 0
    for category in seen_categories:
        n = len(parsed[category])
        total += n
        singular, plural = CATEGORY_LABELS.get(
            category, (category.lower(), category.lower() + "s")
        )
        count_parts.append(f"{n} {singular if n == 1 else plural}")

    lines.append(f"*{', '.join(count_parts)} — {total} commits total*")
    lines.append("")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate release notes from conventional commits"
    )
    parser.add_argument(
        "--range",
        dest="commit_range",
        help="Git commit range (e.g. v1.0.0..HEAD). Omit for full history.",
    )
    parser.add_argument(
        "--version",
        required=True,
        help="Version tag for the release header (e.g. v2026.04.02)",
    )
    parser.add_argument(
        "--output",
        help="Output file path. Prints to stdout if omitted.",
    )
    args = parser.parse_args()

    commits = get_commits(args.commit_range)
    parsed = parse_commits(commits)
    notes = format_notes(parsed, args.version)

    if args.output:
        Path(args.output).write_text(notes)
    else:
        print(notes, end="")


if __name__ == "__main__":
    main()

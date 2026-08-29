#!/usr/bin/env python3
"""Report active SKILL.md size alongside Hermes curator telemetry.

Usage: audit_library.py [--skills-dir PATH] [--usage-json PATH]

By default, skills are read from $HERMES_HOME/skills (or ~/.hermes/skills) and
telemetry is read from `hermes curator usage --json`. --usage-json makes the
script deterministic for tests and offline review.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


def default_skills_dir() -> Path:
    return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser() / "skills"


def manifest_skill_name(text: str, source: Path) -> str:
    """Read the canonical skill identity from YAML frontmatter without PyYAML."""
    if not text.startswith("---"):
        raise ValueError(f"{source}: frontmatter must start with ---")
    for line in text.splitlines()[1:]:
        if line.strip() == "---":
            break
        match = re.fullmatch(r"name:\s*(.+?)\s*", line)
        if match:
            name = match.group(1).strip().strip("\"'")
            if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
                return name
            raise ValueError(f"{source}: invalid frontmatter name {name!r}")
    raise ValueError(f"{source}: missing frontmatter name")


def load_usage(usage_json: Path | None) -> dict[str, dict[str, Any]]:
    """Load curator records keyed by skill name."""
    if usage_json is None:
        result = subprocess.run(
            ["hermes", "curator", "usage", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "hermes curator usage failed")
        raw = result.stdout
    else:
        raw = usage_json.read_text(encoding="utf-8")
    records = json.loads(raw)
    if not isinstance(records, list):
        raise ValueError("curator usage JSON must be a list")
    return {
        record["name"]: record
        for record in records
        if isinstance(record, dict) and isinstance(record.get("name"), str)
    }


def skill_files(skills_dir: Path) -> list[Path]:
    """Return active skill manifests, excluding curator-managed hidden trees."""
    return sorted(
        path
        for path in skills_dir.rglob("SKILL.md")
        if not any(part.startswith(".") for part in path.relative_to(skills_dir).parts)
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-dir", type=Path, default=default_skills_dir())
    parser.add_argument("--usage-json", type=Path)
    args = parser.parse_args(argv)
    skills_dir = args.skills_dir.expanduser().resolve()
    if not skills_dir.is_dir():
        print(f"MISSING SKILL LIBRARY: {skills_dir}", file=sys.stderr)
        return 1
    try:
        usage = load_usage(args.usage_json.expanduser() if args.usage_json else None)
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"USAGE TELEMETRY ERROR: {exc}", file=sys.stderr)
        return 1

    rows: list[tuple[int, int, str, str, str, str, str]] = []
    for skill_file in skill_files(skills_dir):
        text = skill_file.read_text(encoding="utf-8")
        try:
            skill_name = manifest_skill_name(text, skill_file)
        except ValueError as exc:
            print(f"MANIFEST ERROR: {exc}", file=sys.stderr)
            return 1
        relative = skill_file.parent.relative_to(skills_dir).as_posix()
        metadata = usage.get(skill_name, {})
        rows.append(
            (
                len(text),
                int(metadata.get("use_count", 0) or 0),
                str(metadata.get("last_activity_at") or "-"),
                str(metadata.get("state") or "unknown"),
                str(metadata.get("provenance") or "unknown"),
                relative,
                skill_name,
            )
        )

    print(" chars  uses  last-activity               state     provenance  skill")
    for chars, uses, activity, state, provenance, relative, _name in sorted(rows, reverse=True):
        print(f"{chars:>6}  {uses:>4}  {activity:<26}  {state:<8}  {provenance:<10}  {relative}")
    print(f"total skills: {len(rows)}")
    print(f"total chars: {sum(row[0] for row in rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

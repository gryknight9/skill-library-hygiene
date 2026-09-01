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
import unicodedata
from pathlib import Path
from typing import Any, Sequence


def default_skills_dir() -> Path:
    hermes_home = os.environ.get("HERMES_HOME", "").strip() or "~/.hermes"
    return Path(hermes_home).expanduser() / "skills"


def display_text(value: str) -> str:
    """Render terminal-visible text without Unicode control characters."""
    return "".join(
        f"\\x{ord(char):02x}" if unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} else char
        for char in value
    )


def display_path(path: Path) -> str:
    """Render a filesystem path safely for terminal output."""
    return display_text(str(path))


def manifest_skill_name(text: str, source: Path) -> str:
    """Read the canonical skill identity from YAML frontmatter without PyYAML."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"{display_path(source)}: frontmatter must start with ---")
    found_end = False
    parsed_name: str | None = None
    for line in lines[1:]:
        if line.strip() == "---":
            found_end = True
            break
        match = re.fullmatch(r"name:\s*(.+?)\s*", line)
        if match:
            raw_name = match.group(1).strip()
            if raw_name[:1] in {"\"", "'"}:
                if len(raw_name) < 2 or raw_name[-1] != raw_name[0]:
                    raise ValueError(f"invalid quoted frontmatter name in {display_path(source)}")
                raw_name = raw_name[1:-1]
            name = raw_name
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
                raise ValueError(f"{display_path(source)}: invalid frontmatter name {name!r}")
            if parsed_name is not None:
                raise ValueError(f"{display_path(source)}: duplicate frontmatter name")
            parsed_name = name
    if not found_end:
        raise ValueError(f"{display_path(source)}: unterminated frontmatter")
    if parsed_name is None:
        raise ValueError(f"{display_path(source)}: missing frontmatter name")
    return parsed_name


def load_usage(
    usage_json: Path | None,
    hermes_home: Path | None = None,
) -> dict[str, dict[str, Any]]:
    """Load validated curator records keyed by unique skill name."""
    if usage_json is None:
        env = os.environ.copy()
        if hermes_home is not None:
            env["HERMES_HOME"] = str(hermes_home)
        result = subprocess.run(
            ["hermes", "curator", "usage", "--json"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
            env=env,
        )
        if result.returncode:
            stderr = result.stderr.strip()
            raise RuntimeError(repr(stderr) if stderr else "hermes curator usage failed")
        raw = result.stdout
    else:
        raw = usage_json.read_text(encoding="utf-8")
    records = json.loads(raw)
    if not isinstance(records, list):
        raise ValueError("curator usage JSON must be a list")
    usage: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            raise ValueError(f"invalid curator usage record: {record!r}")
        if not isinstance(record.get("name"), str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9_-]*", record["name"]
        ):
            raise ValueError(f"curator usage record has invalid name: {record!r}")
        name = record["name"]
        if any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in name):
            raise ValueError(f"curator usage record has invalid name: {name!r}")
        if name in usage:
            raise ValueError(f"duplicate curator usage record: {name!r}")
        count = record.get("use_count", 0)
        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError(f"invalid use_count for {name!r}: {count!r}")
        for field in ("last_activity_at", "state", "provenance"):
            value = record.get(field)
            if value is not None and (
                not isinstance(value, str)
                or any(unicodedata.category(char) in {"Cc", "Cf", "Zl", "Zp"} for char in value)
            ):
                raise ValueError(f"invalid {field} for {name!r}: {value!r}")
        usage[name] = record
    return usage


def skill_files(skills_dir: Path) -> list[Path]:
    """Return active manifests and reject symlink escapes."""
    resolved_root = skills_dir.resolve()
    files = []
    for path in skills_dir.rglob("SKILL.md"):
        if any(part.startswith(".") for part in path.relative_to(skills_dir).parts):
            continue
        try:
            path.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError(f"manifest escapes skill library: {display_path(path)}") from exc
        files.append(path)
    return sorted(files)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-dir", type=Path, default=default_skills_dir())
    parser.add_argument("--usage-json", type=Path)
    args = parser.parse_args(argv)
    skills_dir = args.skills_dir.expanduser().resolve()
    if not skills_dir.is_dir():
        print(f"MISSING SKILL LIBRARY: {display_path(skills_dir)}", file=sys.stderr)
        return 1
    try:
        usage = load_usage(
            args.usage_json.expanduser() if args.usage_json else None,
            skills_dir.parent,
        )
        manifest_files = skill_files(skills_dir)
    except (
        OSError,
        UnicodeError,
        ValueError,
        json.JSONDecodeError,
        RuntimeError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(f"SKILL LIBRARY ERROR: {display_text(str(exc))}", file=sys.stderr)
        return 1

    rows: list[tuple[int, int, str, str, str, str, str]] = []
    manifest_names: set[str] = set()
    for skill_file in manifest_files:
        try:
            text = skill_file.read_text(encoding="utf-8")
            skill_name = manifest_skill_name(text, skill_file)
        except (OSError, UnicodeError, ValueError) as exc:
            print(f"MANIFEST ERROR: {display_text(str(exc))}", file=sys.stderr)
            return 1
        if skill_name in manifest_names:
            print(
                f"MANIFEST ERROR: duplicate skill name {skill_name!r}",
                file=sys.stderr,
            )
            return 1
        manifest_names.add(skill_name)
        relative = skill_file.parent.relative_to(skills_dir).as_posix()
        metadata = usage.get(skill_name, {})
        rows.append(
            (
                len(text),
                int(metadata.get("use_count", 0) or 0),
                display_text(str(metadata.get("last_activity_at") or "-")),
                display_text(str(metadata.get("state") or "unknown")),
                display_text(str(metadata.get("provenance") or "unknown")),
                display_text(relative),
                skill_name,
            )
        )

    print(" chars  uses  last-activity               state     provenance  skill")
    for row in sorted(rows, reverse=True):
        chars, uses, activity, state, provenance, relative, _name = row
        print(
            f"{chars:>6}  {uses:>4}  {activity:<26}  "
            f"{state:<8}  {provenance:<10}  {relative}"
        )
    print(f"total skills: {len(rows)}")
    print(f"total chars: {sum(row[0] for row in rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

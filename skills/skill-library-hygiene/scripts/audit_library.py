#!/usr/bin/env python3
"""Report active SKILL.md size alongside Hermes curator telemetry.

Usage: audit_library.py [--skills-dir PATH] [--hermes-home PATH]
                       [--usage-json PATH]

By default, skills are read from $HERMES_HOME/skills (or ~/.hermes/skills) and
telemetry is read from `hermes curator usage --json`. --usage-json makes the
script deterministic for tests and offline review.

Telemetry is always collected from the profile that owns the audited skills
directory: the curator subprocess runs with HERMES_HOME pinned to that profile
rather than inheriting the ambient environment. Use --hermes-home only when the
skills directory is not a profile's `skills/` child; a --hermes-home that
disagrees with --skills-dir is rejected as ambiguous.

Manifest discovery is root-isolated: every SKILL.md is resolved and must remain
below the resolved skills directory. Symlinks within the library are allowed;
any manifest resolving outside it aborts the audit rather than reporting
out-of-library data as library data.

Curator telemetry is schema-validated before the report is built: unique
non-empty string names, non-negative integer use counts (booleans rejected),
and control-character-free bounded display fields. Malformed telemetry exits 1
with a diagnostic naming the offending record instead of crashing mid-report or
silently preferring one of several duplicates. Unknown fields are preserved.
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


def default_hermes_home() -> Path:
    return Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()


def default_skills_dir() -> Path:
    return default_hermes_home() / "skills"


def derive_hermes_home(skills_dir: Path) -> Path | None:
    """Infer the owning profile home from a resolved skills directory.

    A Hermes profile always stores manifests in `<HERMES_HOME>/skills`, so the
    parent of a directory literally named `skills` is the profile home. Any
    other layout is not derivable and must be stated explicitly.
    """
    if skills_dir.name == "skills":
        return skills_dir.parent
    return None


def resolve_telemetry_home(skills_dir: Path, hermes_home: Path | None) -> Path:
    """Pick the profile whose curator telemetry describes `skills_dir`.

    Raises ValueError on ambiguous combinations rather than silently joining
    manifests from one profile to usage records from another.
    """
    derived = derive_hermes_home(skills_dir)
    if hermes_home is None:
        if derived is None:
            raise ValueError(
                f"cannot derive a Hermes profile from {skills_dir} "
                "(expected a directory named 'skills'); pass --hermes-home "
                "or --usage-json"
            )
        return derived
    explicit = hermes_home.expanduser().resolve()
    if derived is not None and derived != explicit:
        raise ValueError(
            f"ambiguous profile: --skills-dir {skills_dir} belongs to {derived} "
            f"but --hermes-home is {explicit}"
        )
    if not (explicit / "skills").is_dir():
        raise ValueError(f"--hermes-home {explicit} has no skills directory")
    return explicit


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


MAX_DISPLAY_CHARS = 200
# Table cells are single-line, so tab and newline are corrupting too — no
# whitespace carve-out here.
CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f-\x9f]")
DISPLAY_FIELDS = ("state", "provenance", "last_activity_at")


def checked_display(record_index: int, field: str, value: Any) -> str | None:
    """Accept a display field only when it is safe to print in a table row.

    Curator output reaches a terminal verbatim, so control characters (ANSI
    escapes, newlines that break row alignment) are rejected rather than
    rendered. Absent/null is legal and rendered as a placeholder later.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            f"record {record_index}: {field} must be a string or null, "
            f"got {type(value).__name__}"
        )
    if len(value) > MAX_DISPLAY_CHARS:
        raise ValueError(
            f"record {record_index}: {field} exceeds {MAX_DISPLAY_CHARS} characters"
        )
    if CONTROL_CHARS.search(value):
        raise ValueError(
            f"record {record_index}: {field} contains control characters"
        )
    return value


def validate_records(records: Any) -> dict[str, dict[str, Any]]:
    """Schema-check curator telemetry and key it by unique skill name.

    Fails closed on malformed input instead of crashing mid-report or silently
    preferring one of several duplicate records. Unknown extra fields are
    preserved untouched so new curator fields do not break the audit.
    """
    if not isinstance(records, list):
        raise ValueError("curator usage JSON must be a list")
    usage: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ValueError(
                f"record {index}: must be an object, got {type(record).__name__}"
            )
        name = record.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"record {index}: name must be a non-empty string")
        if CONTROL_CHARS.search(name) or len(name) > MAX_DISPLAY_CHARS:
            raise ValueError(f"record {index}: unusable name {name!r}")
        if name in usage:
            raise ValueError(
                f"record {index}: duplicate name {name!r}; curator telemetry must "
                "have one record per skill"
            )
        count = record.get("use_count", 0)
        if count is None:
            count = 0
        # bool is a subclass of int; True must not silently become 1 use.
        if isinstance(count, bool) or not isinstance(count, int):
            raise ValueError(
                f"record {index} ({name}): use_count must be an integer, "
                f"got {count!r}"
            )
        if count < 0:
            raise ValueError(
                f"record {index} ({name}): use_count must not be negative, got {count}"
            )
        checked = dict(record)
        checked["use_count"] = count
        for field in DISPLAY_FIELDS:
            checked[field] = checked_display(index, field, record.get(field))
        usage[name] = checked
    return usage


def load_usage(
    usage_json: Path | None, hermes_home: Path | None = None
) -> dict[str, dict[str, Any]]:
    """Load curator records keyed by skill name.

    When reading live telemetry, HERMES_HOME is pinned to `hermes_home` so the
    curator reports on the profile being audited instead of whichever profile
    happens to own the ambient environment.
    """
    if usage_json is None:
        if hermes_home is None:
            raise ValueError("live curator telemetry requires a resolved HERMES_HOME")
        env = dict(os.environ)
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
            raise RuntimeError(result.stderr.strip() or "hermes curator usage failed")
        raw = result.stdout
    else:
        raw = usage_json.read_text(encoding="utf-8")
    records = json.loads(raw)
    return validate_records(records)


def contained_manifest(path: Path, resolved_root: Path) -> Path | None:
    """Return the resolved manifest only when it stays below `resolved_root`.

    `Path.resolve()` collapses every symlink in the chain, so one containment
    check covers both a symlinked SKILL.md and a symlinked parent directory.
    In-root symlinks remain legal; escapes do not. Mirrors the policy already
    enforced by verify_ptrs.safe_target().
    """
    try:
        resolved = path.resolve(strict=True)
    except OSError:
        return None
    try:
        resolved.relative_to(resolved_root)
    except ValueError:
        return None
    return resolved


def skill_files(skills_dir: Path) -> tuple[list[Path], list[Path]]:
    """Split discovered manifests into contained and escaping paths.

    Hidden trees (curator archives) are excluded before any resolution, so an
    archived skill is never reported as an escape.
    """
    resolved_root = skills_dir.resolve()
    contained: list[Path] = []
    escaped: list[Path] = []
    for path in sorted(skills_dir.rglob("SKILL.md")):
        if any(part.startswith(".") for part in path.relative_to(skills_dir).parts):
            continue
        if contained_manifest(path, resolved_root) is None:
            escaped.append(path)
        else:
            contained.append(path)
    return contained, escaped


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skills-dir", type=Path, default=default_skills_dir())
    parser.add_argument(
        "--hermes-home",
        type=Path,
        help="Profile home supplying curator telemetry. Defaults to the parent "
        "of --skills-dir. Must not contradict --skills-dir.",
    )
    parser.add_argument("--usage-json", type=Path)
    args = parser.parse_args(argv)
    skills_dir = args.skills_dir.expanduser().resolve()
    if not skills_dir.is_dir():
        print(f"MISSING SKILL LIBRARY: {skills_dir}", file=sys.stderr)
        return 1
    telemetry_home: Path | None = None
    if args.usage_json is None:
        try:
            telemetry_home = resolve_telemetry_home(skills_dir, args.hermes_home)
        except ValueError as exc:
            print(f"PROFILE RESOLUTION ERROR: {exc}", file=sys.stderr)
            return 1
    elif args.hermes_home is not None:
        print(
            "PROFILE RESOLUTION ERROR: --usage-json and --hermes-home are mutually "
            "exclusive; the JSON file already fixes the telemetry source",
            file=sys.stderr,
        )
        return 1
    try:
        usage = load_usage(
            args.usage_json.expanduser() if args.usage_json else None,
            telemetry_home,
        )
    except (OSError, ValueError, json.JSONDecodeError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"USAGE TELEMETRY ERROR: {exc}", file=sys.stderr)
        return 1

    rows: list[tuple[int, int, str, str, str, str, str]] = []
    manifests, escaped = skill_files(skills_dir)
    if escaped:
        print("ESCAPING MANIFESTS (resolve outside the skill library):", file=sys.stderr)
        for path in escaped:
            print(
                f" - {path.relative_to(skills_dir).as_posix()} -> "
                f"{os.path.realpath(path)}",
                file=sys.stderr,
            )
        print(
            "Refusing to audit: these would report out-of-library data as library "
            "data. Remove or re-point the symlinks, then re-run.",
            file=sys.stderr,
        )
        return 1
    for skill_file in manifests:
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
                # Validated as a non-negative int by validate_records().
                metadata.get("use_count", 0),
                metadata.get("last_activity_at") or "-",
                metadata.get("state") or "unknown",
                metadata.get("provenance") or "unknown",
                relative,
                skill_name,
            )
        )

    source = str(telemetry_home) if telemetry_home else f"file:{args.usage_json}"
    print(f"skills dir: {skills_dir}")
    print(f"telemetry source: {source}")
    print(" chars  uses  last-activity               state     provenance  skill")
    for chars, uses, activity, state, provenance, relative, _name in sorted(rows, reverse=True):
        print(f"{chars:>6}  {uses:>4}  {activity:<26}  {state:<8}  {provenance:<10}  {relative}")
    print(f"total skills: {len(rows)}")
    print(f"total chars: {sum(row[0] for row in rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

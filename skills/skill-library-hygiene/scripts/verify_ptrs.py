#!/usr/bin/env python3
"""Validate local and cross-skill reference pointers in a SKILL.md.

Usage: verify_ptrs.py <skill-dir> [skill-library-root]
  <skill-dir>          Directory containing the SKILL.md to check.
  [skill-library-root] Root used for cross-skill pointers. Defaults to
                       $HERMES_HOME/skills, then ~/.hermes/skills.

Pointer forms:
  references/topic.md                 Local to <skill-dir>.
  other-skill/references/topic.md     Relative to the library root.

Exit code: 0 when every pointer resolves safely; 1 for a missing/unsafe pointer
or missing/unreadable SKILL.md; 2 for invalid command-line arguments.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from pathlib import Path
from typing import Iterable, NamedTuple, Sequence


LOCAL_POINTER = re.compile(
    r"(?<![\w/-])(?P<pointer>references/(?P<relative>[A-Za-z0-9_.\-/]+\.md))(?![A-Za-z0-9_.\-/])"
)
CROSS_POINTER = re.compile(
    r"(?<![\w/-])(?P<pointer>(?P<skill>[A-Za-z0-9_-]+)/references/"
    r"(?P<relative>[A-Za-z0-9_.\-/]+\.md))"
)
ABSOLUTE_POINTER = re.compile(
    r"(?<![\w:/-])(?P<pointer>/[A-Za-z0-9_.\-/]*references/"
    r"[A-Za-z0-9_.\-/]+\.md)"
)


REFERENCE_CANDIDATE = re.compile(
    r"(?<![\w/-])(?P<pointer>(?:[^\n/<>`]+/)*references/"
    r"[^\n<>`]*?\.md[A-Za-z0-9_.~?+:\-]*)(?![A-Za-z0-9_.~?+:\-])"
)


MALFORMED_SUFFIX = re.compile(
    r"(?P<pointer>(?:[A-Za-z0-9_-]+/)?references/"
    r"[A-Za-z0-9_.\-/]+\.md[A-Za-z0-9_%~?+:\-/]+)"
)


class Pointer(NamedTuple):
    """A pointer and the root that constrains it."""

    kind: str
    text: str
    relative: Path


def default_library_root() -> Path:
    """Return the active Hermes home skill root without assuming a profile."""
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


def parse_pointers(body: str) -> list[Pointer]:
    """Extract the two documented pointer forms without duplicate entries."""
    pointers: set[Pointer] = set()
    for match in LOCAL_POINTER.finditer(body):
        pointers.add(
            Pointer("local", match.group("pointer"), Path(match.group("pointer")))
        )
    for match in CROSS_POINTER.finditer(body):
        pointers.add(
            Pointer(
                "cross-skill",
                match.group("pointer"),
                Path(match.group("skill")) / "references" / match.group("relative"),
            )
        )
    for match in ABSOLUTE_POINTER.finditer(body):
        pointers.add(
            Pointer("unsafe", match.group("pointer"), Path(match.group("pointer")))
        )
    known = {pointer.text for pointer in pointers}
    for match in REFERENCE_CANDIDATE.finditer(body):
        text = match.group("pointer")
        if text not in known:
            pointers.add(Pointer("unsafe", text, Path(text)))
    for match in MALFORMED_SUFFIX.finditer(body):
        text = match.group("pointer")
        if text not in known:
            pointers.add(Pointer("unsafe", text, Path(text)))
    return sorted(pointers, key=lambda pointer: (pointer.kind, pointer.text))


def safe_target(root: Path, relative: Path) -> Path | None:
    """Resolve a pointer only when it remains below root after symlinks."""
    if relative.is_absolute() or ".." in relative.parts:
        return None
    resolved_root = root.resolve()
    target = (resolved_root / relative).resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError:
        return None
    return target


def manifest_skill_name(skill_file: Path) -> str:
    """Read and validate the target skill's frontmatter identity."""
    text = skill_file.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError("frontmatter must start with ---")
    name: str | None = None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = re.fullmatch(r"name:\s*(.+?)\s*", line)
        if match:
            if name is not None:
                raise ValueError("duplicate frontmatter name")
            raw_name = match.group(1).strip()
            if raw_name[:1] in {"\"", "'"}:
                if len(raw_name) < 2 or raw_name[-1] != raw_name[0]:
                    raise ValueError("invalid quoted frontmatter name")
                raw_name = raw_name[1:-1]
            name = raw_name
    else:
        raise ValueError("unterminated frontmatter")
    if name is None or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise ValueError("invalid or missing frontmatter name")
    return name


def verify(
    skill_dir: Path, library_root: Path
) -> tuple[list[str], list[str], list[str]]:
    """Return missing local, missing cross-skill, and unsafe pointer lists."""
    skill_file = skill_dir / "SKILL.md"
    try:
        body = skill_file.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise OSError(
            f"cannot read {display_path(skill_file)}: {display_text(str(exc))}"
        ) from exc

    local_failures: list[str] = []
    cross_failures: list[str] = []
    unsafe_failures: list[str] = []
    for pointer in parse_pointers(body):
        if pointer.kind == "unsafe":
            reason = "absolute path" if pointer.text.startswith("/") else "invalid pointer"
            unsafe_failures.append(f"{pointer.text} ({reason})")
            continue
        root = skill_dir if pointer.kind == "local" else library_root
        if pointer.kind == "cross-skill":
            skill_name = pointer.relative.parts[0]
            skill_manifest = safe_target(library_root, Path(skill_name) / "SKILL.md")
            if skill_manifest is None or not skill_manifest.is_file():
                cross_failures.append(
                    f"{pointer.text} (target is not an installed skill)"
                )
                continue
            try:
                target_name = manifest_skill_name(skill_manifest)
            except (OSError, UnicodeError, ValueError):
                cross_failures.append(
                    f"{pointer.text} (target has invalid skill manifest)"
                )
                continue
            if target_name != pointer.relative.parts[0]:
                cross_failures.append(
                    f"{pointer.text} (target manifest name mismatch: {target_name})"
                )
                continue
        target = safe_target(root, pointer.relative)
        if target is None:
            failure = f"{pointer.text} (unsafe path)"
        elif not target.is_file():
            failure = pointer.text
        else:
            continue
        if pointer.kind == "local":
            local_failures.append(failure)
        else:
            cross_failures.append(failure)
    return local_failures, cross_failures, unsafe_failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_dir", type=Path, help="directory containing SKILL.md")
    parser.add_argument(
        "library_root",
        nargs="?",
        type=Path,
        default=default_library_root(),
        help="cross-skill library root (default: $HERMES_HOME/skills)",
    )
    return parser


def print_failures(heading: str, failures: Iterable[str]) -> None:
    failures = list(failures)
    if failures:
        print(f"{heading}:")
        for failure in failures:
            print(f" - {display_text(failure)}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        skill_dir = args.skill_dir.expanduser().resolve()
        library_root = args.library_root.expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        print(f"UNREADABLE SKILL.md: {display_text(str(exc))}")
        return 1
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        print(f"MISSING SKILL.md: {display_path(skill_file)}")
        return 1

    try:
        local_failures, cross_failures, unsafe_failures = verify(
            skill_dir, library_root
        )
    except (OSError, RuntimeError) as exc:
        print(f"UNREADABLE SKILL.md: {display_text(str(exc))}")
        return 1

    print_failures("LOCAL MISSING OR UNSAFE", local_failures)
    print_failures("CROSS-SKILL MISSING OR UNSAFE", cross_failures)
    print_failures("UNSAFE ABSOLUTE POINTERS", unsafe_failures)
    if not local_failures and not cross_failures and not unsafe_failures:
        print("all reference pointers resolve safely")
    try:
        chars = len(skill_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError) as exc:
        print(f"UNREADABLE SKILL.md: {display_text(str(exc))}")
        return 1
    print(f"chars: {chars}")
    return 1 if local_failures or cross_failures or unsafe_failures else 0


if __name__ == "__main__":
    sys.exit(main())

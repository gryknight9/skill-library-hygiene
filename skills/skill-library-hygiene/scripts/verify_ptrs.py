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
from pathlib import Path
from typing import Iterable, NamedTuple, Sequence


LOCAL_POINTER = re.compile(
    r"(?<![\w/-])(?P<pointer>references/(?P<relative>[A-Za-z0-9_.\-/]+\.md))"
)
CROSS_POINTER = re.compile(
    r"(?<![\w/-])(?P<pointer>(?P<skill>[A-Za-z0-9_-]+)/references/"
    r"(?P<relative>[A-Za-z0-9_.\-/]+\.md))"
)
ABSOLUTE_POINTER = re.compile(
    r"(?<![\w:/-])(?P<pointer>/[A-Za-z0-9_.\-/]*references/"
    r"[A-Za-z0-9_.\-/]+\.md)"
)


class Pointer(NamedTuple):
    """A pointer and the root that constrains it."""

    kind: str
    text: str
    relative: Path


def default_library_root() -> Path:
    """Return the active Hermes home skill root without assuming a profile."""
    hermes_home = Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser()
    return hermes_home / "skills"


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
        pointers.add(Pointer("unsafe", match.group("pointer"), Path(match.group("pointer"))))
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


def installed_skill_dir(library_root: Path, skill_name: str) -> Path | None:
    """Resolve an installed cross-skill target below the library root.

    This checker is deliberately not a general manifest schema linter; Hermes'
    native `skills check` owns that. For pointer-integrity purposes, an
    installed skill is a contained directory with its own contained SKILL.md.
    In-root directory/file symlinks are allowed, while escapes and broken links
    are rejected.
    """
    skill_dir = safe_target(library_root, Path(skill_name))
    if skill_dir is None or not skill_dir.is_dir():
        return None
    manifest = safe_target(skill_dir, Path("SKILL.md"))
    if manifest is None or not manifest.is_file():
        return None
    return skill_dir


def verify(skill_dir: Path, library_root: Path) -> tuple[list[str], list[str], list[str]]:
    """Return missing local, missing cross-skill, and unsafe pointer lists."""
    skill_file = skill_dir / "SKILL.md"
    try:
        body = skill_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"cannot read {skill_file}: {exc}") from exc

    local_failures: list[str] = []
    cross_failures: list[str] = []
    unsafe_failures: list[str] = []
    for pointer in parse_pointers(body):
        if pointer.kind == "unsafe":
            unsafe_failures.append(f"{pointer.text} (absolute path)")
            continue
        root = skill_dir if pointer.kind == "local" else library_root
        if pointer.kind == "cross-skill":
            target_skill = installed_skill_dir(library_root, pointer.relative.parts[0])
            if target_skill is None:
                cross_failures.append(
                    f"{pointer.text} (target is not an installed skill)"
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
            print(f" - {failure}")


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    skill_dir = args.skill_dir.expanduser().resolve()
    library_root = args.library_root.expanduser().resolve()
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        print(f"MISSING SKILL.md: {skill_file}")
        return 1

    try:
        local_failures, cross_failures, unsafe_failures = verify(skill_dir, library_root)
    except OSError as exc:
        print(f"UNREADABLE SKILL.md: {exc}")
        return 1

    print_failures("LOCAL MISSING OR UNSAFE", local_failures)
    print_failures("CROSS-SKILL MISSING OR UNSAFE", cross_failures)
    print_failures("UNSAFE ABSOLUTE POINTERS", unsafe_failures)
    if not local_failures and not cross_failures and not unsafe_failures:
        print("all reference pointers resolve safely")
    print(f"chars: {len(skill_file.read_text(encoding='utf-8'))}")
    return 1 if local_failures or cross_failures or unsafe_failures else 0


if __name__ == "__main__":
    sys.exit(main())

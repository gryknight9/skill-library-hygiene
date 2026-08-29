#!/usr/bin/env python3
"""Verify local references/*.md pointers in a SKILL.md resolve, and report body size.

Usage: verify_ptrs.py <skill-dir> [cross-skill-base]
  <skill-dir>        directory containing SKILL.md to check
  [cross-skill-base] optional root (default ~/.hermes/skills) used to resolve
                     cross-skill pointers like other-skill/references/foo.md

Exit code: 0 = all local pointers OK (dangling cross-pointers are reported but
do not fail), 1 = at least one LOCAL pointer is missing or SKILL.md absent.

Always run this as a file (`python3 scripts/verify_ptrs.py ...`) — never inline
via heredoc or execute_code; sandbox quoting corrupts string ops in ad-hoc
pointer-check snippets and produces false failures.
"""

import os
import re
import sys


def main() -> int:
    """Run the pointer verification and exit with the appropriate code."""
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    skill_dir = os.path.abspath(sys.argv[1])
    base = (
        os.path.abspath(sys.argv[2])
        if len(sys.argv) > 2
        else os.path.expanduser("~/.hermes/skills")
    )
    path = os.path.join(skill_dir, "SKILL.md")
    if not os.path.isfile(path):
        print("MISSING SKILL.md:", path)
        return 1
    body = open(path).read()
    # references/<...>.md NOT preceded by word char, '/', or '-' so we don't
    # catch tails of longer tokens like "see-references/foo.md" mid-word.
    ptrs = sorted(set(re.findall(r"(?<![\w/-])references/[A-Za-z0-9_\-./]+\.md", body)))
    local_missing = []
    cross_missing = []
    for p in ptrs:
        if os.path.isfile(os.path.join(skill_dir, p)):
            continue
        alt = os.path.join(base, p)  # pointer written relative to another skill dir
        if os.path.isdir(alt):
            cross_missing.append(p + " (dir exists — no exact file match under it)")
        elif os.path.isfile(alt):
            pass  # resolves from library root; fine
        elif "/" in p and not p.startswith(".."):
            cross_missing.append(p)
        else:
            local_missing.append(p)
    if local_missing:
        print("LOCAL MISSING:")
        for m in local_missing:
            print(" -", m)
    if cross_missing:
        print("CROSS-SKILL UNRESOLVED (flag as fix-or-repoint item):")
        for c in cross_missing:
            print(" -", c)
    if not ptrs:
        print("no references/ pointers found")
    else:
        print(
            f"local pointers OK ({len(ptrs) - len(local_missing)})"
            if not local_missing
            else f"local pointers BROKEN ({len(local_missing)} missing)"
        )
    print("chars:", len(body))
    return 1 if local_missing else 0


if __name__ == "__main__":
    sys.exit(main())

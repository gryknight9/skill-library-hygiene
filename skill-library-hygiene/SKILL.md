---
name: skill-library-hygiene
description: Audit local skills for context cost and stale content.
version: 1.1.0
author: gryknight9, Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [skills, context-window, maintenance]
    related_skills: []
---

# Skill Library Hygiene

Audit and safely restructure the active Hermes profile's local skill library. It measures context cost, uses Hermes’ curator telemetry, and checks reference-file links without treating unused skills as disposable.

## When to Use

- Context cost is high and installed skills may be contributing.
- A user wants to find stale or oversized local skills.
- Before restructuring, archiving, or deleting an existing local skill.

Don't use for authoring an in-repository skill or for automatic deletion. Use the in-repo skill-authoring workflow for repository-managed skills.

## Prerequisites

- Run commands through `terminal` in the intended Hermes profile. The scripts resolve the library from `$HERMES_HOME`, falling back to `~/.hermes`; pass an explicit directory when auditing another profile.
- Python is invoked explicitly: use `python3` on Linux/macOS and `py -3` on Windows. The scripts themselves use only the standard library.
- `hermes curator usage --json` must be available. It is the telemetry source of truth; do not invent or read a `.usage.json` sidecar file.

## Procedure

1. **Take a recoverable snapshot before changing anything.** Run `terminal(command="hermes curator backup --reason 'before skill-library hygiene pass'", timeout=30)` after the user approves the scope. Completion: curator reports the snapshot.
2. **Measure active skills and curator telemetry.** On Linux/macOS, run `terminal(command="python3 scripts/audit_library.py", timeout=30)` from this skill's directory. On Windows, run `terminal(command="py -3 scripts\\audit_library.py", timeout=30)`. To audit another profile, add `--skills-dir /path/to/profile/skills`. Completion: every non-archived `SKILL.md` is listed with chars, use count, last activity, state, provenance, and relative path.
3. **Choose candidates, not victims.** Treat more than 15k chars as a restructure candidate and 9–15k as a review candidate. Zero usage is only a prompt for review: consult provenance, pinned state, cron references, and last activity before proposing archive or deletion.
4. **Restructure one skill at a time.** Keep triggers, safety constraints, essential environment facts, and a concise reference index in `SKILL.md`. Move long examples, incident history, API listings, and edge-case catalogs into `references/` or `references/archive/`. Use `patch` or `write_file` for the change; do not leave `SKILL.md.bak-*` copies inside the live skill tree.
5. **Check reference pointers.** On Linux/macOS, run `terminal(command="python3 scripts/verify_ptrs.py <skill-dir>", timeout=30)`; on Windows use `terminal(command="py -3 scripts\\verify_ptrs.py <skill-dir>", timeout=30)`. Local pointers use `references/<topic>.md`; cross-skill pointers use `other-skill/references/<topic>.md`. Every unresolved or unsafe pointer is a failure. Completion: exit status 0.
6. **Check incoming dependencies and scheduled use.** Use `search_files` to find the skill name in the active profile's other `SKILL.md` files and cron-job configuration before archiving or deleting. Repoint every valid dependency or record it as a blocker. Completion: every incoming reference is accounted for.
7. **Archive or delete only with explicit user approval.** Prefer the reversible `terminal(command="hermes curator archive <skill>", timeout=30)` for a reviewed candidate. Use `hermes curator restore <skill>` to undo an archive. Purge/delete is a separate, explicit decision.

## Pointer Rules

- Local links must remain below the touched skill directory: `references/<topic>.md`.
- Cross-skill links must remain below the active library root: `other-skill/references/<topic>.md`.
- `..`, absolute paths, and symlinks resolving outside their allowed root are unsafe and fail validation.
- A prose instruction such as “see skill X” is not a reference pointer. Name the exact file when a linked reference is required.

## Pitfalls

- Profiles have separate homes. A hard-coded `~/.hermes/skills` can audit the wrong library when a named profile is active.
- Curator telemetry includes `use_count`, `view_count`, activity timestamps, provenance, and state. Do not infer staleness from a nonexistent JSON file.
- `hermes curator run --dry-run` previews curator behavior; it does not replace pointer checks after manual restructuring.
- Do not equate an empty reference list with a passing test of the checker. Run the regression tests after modifying either helper script.

## Verification

- `python3 -m unittest discover -s tests -v` passes on Linux/macOS, or `py -3 -m unittest discover -s tests -v` passes on Windows.
- `verify_ptrs.py` returns 0 for every touched skill.
- The before/after report lists the recurring character delta and every changed, archived, or deferred skill.
- The user has approved any archive or deletion, and a curator snapshot exists for structural changes.

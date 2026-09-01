---
name: skill-library-hygiene
description: Audit local skills for context cost and stale content.
version: 1.2.0
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

- Tested with Hermes Agent v0.21.0 (2026.8.31); `hermes curator usage --json` must be available. It is the live telemetry source of truth.
- Run commands through `terminal` in the intended Hermes profile. With no arguments the scripts resolve the library from `$HERMES_HOME`, falling back to `~/.hermes`.
- When auditing another profile, pass its `--skills-dir`. The audit derives and pins that profile's `HERMES_HOME`; it rejects a contradictory explicit `--hermes-home`. Use `--usage-json` only for deterministic tests or offline review.
- Python is invoked explicitly: use `python3` on Linux/macOS and `py -3` on Windows. The scripts themselves use only the standard library.

## Procedure

1. **Take a recoverable snapshot before changing anything.** Run `terminal(command="hermes curator backup --reason 'before skill-library hygiene pass'", timeout=30)` after the user approves the scope. Completion: curator reports the snapshot.
2. **Measure active skills and curator telemetry.** On Linux/macOS, run `terminal(command="python3 scripts/audit_library.py", timeout=30)` from this skill's directory. On Windows, run `terminal(command="py -3 scripts\\audit_library.py", timeout=30)`. To audit another profile, add `--skills-dir /path/to/profile/skills`; do not separately override `HERMES_HOME`. Confirm the printed `skills dir:` and `telemetry source:` identify the same intended profile. Completion: every non-archived, root-contained `SKILL.md` is listed with chars, use count, last activity, state, provenance, and relative path, with no validation error.
3. **Choose candidates, not victims.** Treat more than 15k chars as a restructure candidate and 9–15k as a review candidate. Zero usage is only a prompt for review: consult provenance, pinned state, cron references, and last activity before proposing archive or deletion.
4. **Restructure one skill at a time.** Keep triggers, safety constraints, essential environment facts, and a concise reference index in `SKILL.md`. Move long examples, incident history, API listings, and edge-case catalogs into `references/` or `references/archive/`. Use `patch` or `write_file` for the change; do not leave `SKILL.md.bak-*` copies inside the live skill tree.
5. **Check reference pointers.** On Linux/macOS, run `terminal(command="python3 scripts/verify_ptrs.py <skill-dir>", timeout=30)`; on Windows use `terminal(command="py -3 scripts\\verify_ptrs.py <skill-dir>", timeout=30)`. Local pointers use `references/<topic>.md`; cross-skill pointers use `other-skill/references/<topic>.md`. A cross-skill target must be a contained directory with its own contained `SKILL.md`; arbitrary shared directories are not valid targets. Every unresolved, non-skill, broken, or unsafe pointer is a failure. Completion: exit status 0.
6. **Check incoming dependencies and scheduled use.** Use `search_files` to find the skill name in the active profile's other `SKILL.md` files and cron-job configuration before archiving or deleting. Repoint every valid dependency or record it as a blocker. Completion: every incoming reference is accounted for.
7. **Archive or delete only with explicit user approval.** Prefer the reversible `terminal(command="hermes curator archive <skill>", timeout=30)` for a reviewed candidate. Use `hermes curator restore <skill>` to undo an archive. Purge/delete is a separate, explicit decision.

## Pointer Rules

- Local links must remain below the touched skill directory: `references/<topic>.md`.
- Cross-skill links must remain below the active library root: `other-skill/references/<topic>.md`. The first component must be a structurally installed skill directory containing its own contained `SKILL.md`; full manifest schema validation remains the job of `hermes skills check`.
- `..`, absolute paths, missing files, broken links, non-skill cross-targets, and symlinks resolving outside their allowed root are unsafe and fail validation. In-root symlinks are allowed.
- A prose instruction such as “see skill X” is not a reference pointer. Name the exact file when a linked reference is required.

## Pitfalls

- Profiles have separate homes. A hard-coded `~/.hermes/skills` can audit the wrong library when a named profile is active. Prefer `--skills-dir`; the audit derives the matching telemetry profile and refuses ambiguity.
- Every audited manifest must remain below the library root and have complete `---` frontmatter with exactly one valid top-level `name:`. Use `hermes skills check` for the complete Hermes schema.
- Curator telemetry includes `use_count`, `view_count`, activity timestamps, provenance, and state. The audit rejects duplicates, invalid counts, overlong/control-bearing display fields, and malformed records before emitting a report. Do not infer staleness from a nonexistent JSON file.
- `hermes curator run --dry-run` previews curator behavior; it does not replace pointer checks after manual restructuring.
- Do not equate an empty reference list with a passing test of the checker. Run the regression tests after modifying either helper script.

## Verification

- `python3 -m unittest discover -s tests -v` passes on Linux/macOS, or `py -3 -m unittest discover -s tests -v` passes on Windows.
- `verify_ptrs.py` returns 0 for every touched skill.
- The before/after report lists the recurring character delta and every changed, archived, or deferred skill.
- The user has approved any archive or deletion, and a curator snapshot exists for structural changes.

# Skill Library Hygiene

A profile-aware Hermes Agent skill for reviewing the **operational health** of a local skill library: context cost, curator activity, reference integrity, safe restructuring, and approval-gated lifecycle changes.

It is not a general YAML-schema linter and it does not delete skills automatically.

## What it does

- Measures every active `SKILL.md` file's character count.
- Joins manifests to profile-matched `hermes curator usage --json` telemetry: use count, last activity, state, and provenance.
- Resolves the target profile from the selected skills directory instead of silently inheriting an unrelated `$HERMES_HOME`.
- Validates the subset of manifest frontmatter needed for a safe telemetry join: a complete delimited block with exactly one canonical `name`.
- Rejects malformed, duplicate, unsafe, or terminal-corrupting curator records before building a report.
- Keeps manifest discovery below the selected library root, including after symlink resolution.
- Validates documented local and cross-skill `references/*.md` pointers.
- Requires cross-skill pointers to target structurally installed skills, not arbitrary directories.
- Rejects `..` traversal, absolute pointer paths, broken links, and symlinks that resolve outside the allowed root.
- Treats low-use skills as review candidates, not automatic deletion targets.
- Uses Hermes curator backup/archive/restore operations for reversible lifecycle changes.

## Repository layout

```text
skill-library-hygiene/
├── skills/
│   └── skill-library-hygiene/
│       ├── SKILL.md
│       └── scripts/
│           ├── audit_library.py
│           └── verify_ptrs.py
├── tests/
│   ├── test_audit_library.py
│   └── test_verify_ptrs.py
└── README.md
```

The installable skill is `skills/skill-library-hygiene/SKILL.md`. Its Python helpers use only the standard library.

## Install from a Hermes tap

Subscribe to the tap, search it, and install the identifier Hermes returns:

```bash
hermes skills tap add gryknight9/skill-library-hygiene
hermes skills search skill-library-hygiene --source github --json
# Install the returned identifier:
hermes skills install gryknight9/skill-library-hygiene/skills/skill-library-hygiene
```

To install only this skill without retaining a tap subscription:

```bash
hermes skills install gryknight9/skill-library-hygiene/skills/skill-library-hygiene
```

Hermes security-scans community skills during installation.

## Compatibility

Tested with **Hermes Agent v0.21.0 (2026.8.31), upstream `18a76be1`**. This records the version used for the documented commands and 41-test verification; it is not a claim that earlier releases are incompatible.

The current Hermes Agent documentation is at <https://hermes-agent.nousresearch.com/docs>.

## Prerequisites

- Hermes Agent v0.21.0 or another release providing `hermes curator usage --json`, `hermes skills check`, and `hermes skills audit`.
- Python 3. On Linux/macOS use `python3`; on Windows use `py -3`.
- Run in the intended Hermes profile, or select a profile explicitly with `--skills-dir`.

With no arguments, `audit_library.py` reads `$HERMES_HOME/skills`; when `$HERMES_HOME` is absent, it falls back to `~/.hermes/skills`. `verify_ptrs.py` uses the same default for its optional library-root argument.

## Audit a skill library

### Active profile

From this repository:

```bash
python3 skills/skill-library-hygiene/scripts/audit_library.py
```

### Named profile

Pass its skills directory. The audit derives the owning profile home from the parent directory and pins the curator subprocess to it:

```bash
python3 skills/skill-library-hygiene/scripts/audit_library.py \
  --skills-dir ~/.hermes/profiles/noc/skills
```

Example output:

```text
skills dir: /home/example/.hermes/profiles/noc/skills
telemetry source: /home/example/.hermes/profiles/noc
 chars  uses  last-activity               state     provenance  skill
 18420     1  2026-08-12T19:00:00+00:00  active    agent       devops/network-troubleshooting
  4210    18  2026-08-28T14:00:00+00:00  active    bundled     autonomous-ai-agents/hermes-agent
total skills: 2
total chars: 22630
```

The `skills dir:` and `telemetry source:` headers make the scope visible. Supplying `--skills-dir` for one profile while supplying a contradictory `--hermes-home` is an error; the audit never silently chooses one.

### Custom layout or offline telemetry

`--hermes-home PATH` is for a skills directory whose name/layout does not identify its owning Hermes profile. The path must contain a `skills/` directory. It is normally unnecessary when `--skills-dir` ends in `skills`.

```bash
python3 skills/skill-library-hygiene/scripts/audit_library.py \
  --skills-dir /srv/review-target \
  --hermes-home ~/.hermes/profiles/noc
```

For deterministic tests or offline review, read curator records from a file instead of spawning Hermes:

```bash
python3 skills/skill-library-hygiene/scripts/audit_library.py \
  --skills-dir ~/.hermes/profiles/noc/skills \
  --usage-json ./usage.json
```

`--usage-json` and `--hermes-home` are mutually exclusive because the JSON file already fixes the telemetry source.

## Audit validation and failure behavior

The audit fails closed with exit status 1 instead of emitting a partial or misleading report.

### Profile isolation

Live curator telemetry always runs with `HERMES_HOME` pinned to the profile owning the selected skills directory. This prevents same-named skills in different profiles from receiving the wrong use count, state, activity, or provenance.

### Manifest isolation and identity

Every discovered `SKILL.md` is resolved before it is read. Its final target must remain below the resolved skills root. In-library symlinks are allowed; escaping and broken manifest links abort the audit and are listed on stderr.

For the telemetry join, each manifest must have:

- An opening `---` delimiter line.
- A closing `---` delimiter line.
- Exactly one top-level `name:` field inside that block.
- A canonical name matching `[a-z0-9][a-z0-9_-]*`.

This is deliberately narrower than full Hermes schema validation. Use `hermes skills check` for required descriptions, supported metadata, and the rest of the current Hermes manifest schema.

### Curator record validation

Before any report rows are built, curator JSON must be a list containing one object per unique skill name. The audit requires:

- A non-empty, control-character-free string `name`.
- A non-negative integer `use_count`; booleans and numeric strings are rejected.
- String-or-null `state`, `provenance`, and `last_activity_at` values.
- Display fields no longer than 200 characters and containing no terminal control characters, tabs, or newlines.

Unknown curator fields are preserved so future Hermes additions do not break the audit. Null counts and activity fields are tolerated for never-used skills. Duplicate names and malformed records produce a record-indexed `USAGE TELEMETRY ERROR` and no partial report.

## Example: safely slim a profile

This sequence audits a named profile, creates a recovery point, moves long history into references, and verifies the result. It does not archive or delete anything.

```bash
cd skill-library-hygiene

# 1. Measure the intended profile. Its telemetry profile is derived automatically.
python3 skills/skill-library-hygiene/scripts/audit_library.py \
  --skills-dir ~/.hermes/profiles/noc/skills

# 2. Before structural changes, create a curator-managed recovery point in the
# target profile. Curator itself is still selected through HERMES_HOME.
HERMES_HOME=~/.hermes/profiles/noc hermes curator backup \
  --reason 'before network-troubleshooting reference split'

# 3. Move only long examples/history into references/. Keep triggers, safety
# constraints, and a short reference index in SKILL.md, then validate pointers.
HERMES_HOME=~/.hermes/profiles/noc \
  python3 skills/skill-library-hygiene/scripts/verify_ptrs.py \
  ~/.hermes/profiles/noc/skills/devops/network-troubleshooting \
  ~/.hermes/profiles/noc/skills

# 4. Run the repository regression tests after changing a helper script.
python3 -m unittest discover -s tests -v
```

Expected pointer-check result:

```text
all reference pointers resolve safely
chars: 4820
```

A low use count alone is not an archive/delete signal. Before proposing lifecycle changes, inspect provenance, curator state, active cron references, and incoming cross-skill pointers. Archive only with explicit user approval; use `hermes curator restore <skill>` to undo an archive.

## Pointer rules

`verify_ptrs.py` recognizes two deliberate forms in `SKILL.md` prose:

```text
references/topic.md                     # local to the current skill
other-skill/references/topic.md         # relative to the library root
```

Local pointers must resolve below the current skill directory. Cross-skill pointers must resolve below the selected library root, and their first component must be a structurally installed skill directory containing its own contained `SKILL.md`.

For pointer-integrity purposes, “installed” is a structural check—not full YAML/frontmatter validation. Use `hermes skills check` for schema validation.

The checker fails on:

- Missing reference files.
- `..` traversal.
- Absolute paths.
- Broken links.
- Symlink escapes from the permitted root.
- Cross-skill targets that are ordinary/shared directories rather than installed skills.
- Cross-skill targets whose `SKILL.md` resolves outside that target skill directory.

In-root symlinks remain allowed. Arbitrary shared reference directories are not a supported cross-skill pointer target.

## Relationship to Hermes built-ins

Hermes supplies adjacent capabilities, but their scope is different:

| Tool | Primary purpose | Where this skill adds coverage |
|---|---|---|
| `hermes skills check [name]` | Check installed skills or a named skill against Hermes's current requirements. | Does not provide library-wide context-cost analysis, profile-matched curator-use interpretation, pointer safety checks, or restructure workflow. |
| `hermes skills audit [--deep] [name]` | Audit skills; `--deep` enables AST-level analysis of Python files. | Does not replace profile-aware telemetry joins, reference graph checks, or approval-gated archive decisions. Use it as an optional code-quality gate for a skill with helper scripts. |
| `hermes curator usage --json` | Canonical skill usage, provenance, state, and activity telemetry. | This project validates and joins curator records to contained manifests from the selected profile. |
| `hermes curator backup`, `archive`, `restore` | Reversible lifecycle operations. | This skill defines when to use them: after scope review and explicit approval, with dependencies checked first. |

A practical sequence is:

```bash
# 1. Use Hermes's own package/schema and code-audit checks.
hermes skills check
hermes skills audit --deep skill-library-hygiene

# 2. Measure the profile and verify reference safety.
python3 skills/skill-library-hygiene/scripts/audit_library.py
python3 skills/skill-library-hygiene/scripts/verify_ptrs.py <skill-dir>
```

## Relationship to `agent-skills-lint`

[`swarmclawai/agent-skills-lint`](https://github.com/swarmclawai/agent-skills-lint) is a separate, cross-agent package/schema linter. It validates frontmatter, names, descriptions, empty bodies, filename conventions, and duplicate names; it can also generate indexes and install skills for multiple agent formats.

| Capability | `agent-skills-lint` | Skill Library Hygiene |
|---|---:|---:|
| YAML/frontmatter schema validation | Yes | No general schema linter |
| Name collision detection | Yes | Telemetry duplicates only |
| Description and empty-body diagnostics | Yes | No |
| Cross-agent installation and index generation | Yes | No |
| Hermes profile-aware root via `$HERMES_HOME` | No | Yes |
| Profile-matched curator activity/provenance/state analysis | No | Yes |
| Context-cost and large-skill review | No | Yes |
| Reference pointer, installed-target, traversal, and symlink validation | No | Yes |
| Approval-gated archive/restore workflow | No | Yes |

The tools are complementary, with one compatibility caveat: `agent-skills-lint`'s Hermes flavor currently recognizes only `name`, `description`, and `trigger`. Standard Hermes metadata such as `version`, `author`, `license`, `platforms`, and `metadata` will be reported as unknown frontmatter keys (warnings, or errors in strict mode). Its Hermes install root is also hard-coded to `~/.hermes/skills`, so pass an explicit profile path when working outside the default profile.

Use it as a non-strict structural lint for a specific profile directory, not as a replacement for Hermes's native curator data or this skill's pointer validation:

```bash
# Pin the package version in repeatable automation; do not blindly run @latest.
npx @swarmclawai/agent-skills-lint@0.1.0 \
  --flavor hermes lint ~/.hermes/profiles/noc/skills
```

## Development

```bash
python3 -m py_compile skills/skill-library-hygiene/scripts/*.py tests/*.py
python3 -m unittest discover -s tests -v
python3 skills/skill-library-hygiene/scripts/verify_ptrs.py skills/skill-library-hygiene
```

The 41-test suite covers:

- Profile-root resolution and curator subprocess isolation.
- Curator record schema, uniqueness, count, and terminal-safety validation.
- Complete and unique manifest frontmatter identity.
- Manifest root containment, file/directory symlink escapes, broken links, and permitted in-root symlinks.
- Local and cross-skill pointers, missing targets, installed-skill requirements, traversal, absolute paths, and pointer symlink containment.
- Hidden-tree exclusion and canonical manifest-to-telemetry identity mapping.

## Safety model

- No automatic archive, deletion, or purge.
- Back up before approved structural changes.
- Prefer curator archive over destructive removal.
- Verify every pointer after restructuring.
- Treat zero use as evidence to inspect, not permission to remove.

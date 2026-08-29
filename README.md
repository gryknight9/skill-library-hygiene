# Skill Library Hygiene

A profile-aware Hermes Agent skill for reviewing the **operational health** of a local skill library: context cost, curator activity, reference integrity, safe restructuring, and approval-gated lifecycle changes.

It is not a general YAML-schema linter and it does not delete skills automatically.

## What it does

- Measures every active `SKILL.md` file's character count.
- Joins those manifests to `hermes curator usage --json` telemetry: use count, last activity, state, and provenance.
- Resolves the active profile from `$HERMES_HOME`, rather than assuming `~/.hermes`.
- Validates documented local and cross-skill `references/*.md` pointers.
- Rejects `..` traversal, absolute pointer paths, and symlinks that resolve outside the allowed root.
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

After this repository is public, another Hermes user can subscribe to the tap and install the skill:

```bash
hermes skills tap add gryknight9/skill-library-hygiene
hermes skills install gryknight9/skill-library-hygiene/skill-library-hygiene
```

To install only this skill without retaining a tap subscription:

```bash
hermes skills install gryknight9/skill-library-hygiene/skills/skill-library-hygiene
```

Hermes security-scans community skills during installation. The repository remains private until its GitHub visibility is changed separately.

## Prerequisites

- Hermes Agent with `hermes curator usage --json` available.
- Python 3. On Linux/macOS use `python3`; on Windows use `py -3`.
- Run in the intended Hermes profile. The helpers read `$HERMES_HOME/skills`; when the variable is absent they fall back to `~/.hermes/skills`.

## Example: audit and safely slim a profile

This example audits a named profile, finds a large low-use skill, moves long incident history into a reference file, and verifies the result. It does not archive or delete anything.

```bash
# Enter this repository.
cd skill-library-hygiene

# Audit the profile explicitly. Supplying --skills-dir avoids auditing the
# wrong profile when multiple Hermes profiles are installed.
python3 skills/skill-library-hygiene/scripts/audit_library.py \
  --skills-dir ~/.hermes/profiles/noc/skills

# Example output:
#  chars  uses  last-activity               state     provenance  skill
#  18420     1  2026-08-12T19:00:00+00:00    active    agent       devops/network-troubleshooting
#   4210    18  2026-08-28T14:00:00+00:00    active    bundled     autonomous-ai-agents/hermes-agent
# total skills: 2
# total chars: 22630

# Before any structural change, create a curator-managed recovery point in
# the target profile. Invoke the profile's Hermes command/environment.
HERMES_HOME=~/.hermes/profiles/noc hermes curator backup \
  --reason 'before network-troubleshooting reference split'

# Move only long examples/history into references/; keep triggers, safety
# constraints, and a short reference index in SKILL.md. Then validate pointers.
HERMES_HOME=~/.hermes/profiles/noc \
  python3 skills/skill-library-hygiene/scripts/verify_ptrs.py \
  ~/.hermes/profiles/noc/skills/devops/network-troubleshooting \
  ~/.hermes/profiles/noc/skills

# Run the repository regression tests after changing a helper script.
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

Pointers must resolve below their permitted root. The checker fails on missing files, `..` traversal, absolute paths, and symlink escapes.

## Relationship to Hermes built-ins

Hermes supplies adjacent capabilities, but their scope is different:

| Tool | Primary purpose | Where this skill adds coverage |
|---|---|---|
| `hermes skills check [name]` | Check installed skills or a named skill. | Does not provide library-wide context-cost analysis, curator-use interpretation, pointer safety checks, or restructure workflow. |
| `hermes skills audit [--deep] [name]` | Audit skills; `--deep` enables AST-level analysis of Python files. | Does not replace profile-aware telemetry joins, reference graph checks, or approval-gated archive decisions. Use it as an optional code-quality gate for a skill with helper scripts. |
| `hermes curator usage --json` | Canonical skill usage, provenance, state, and activity telemetry. | This project turns curator records into a size-and-activity report for the active profile. |
| `hermes curator backup`, `archive`, `restore` | Reversible lifecycle operations. | This skill defines when to use them: after scope review and explicit approval, with dependencies checked first. |

A practical sequence is:

```bash
# 1. Use Hermes' own package/audit checks.
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
| Name collision detection | Yes | No |
| Description and empty-body diagnostics | Yes | No |
| Cross-agent installation and index generation | Yes | No |
| Hermes profile-aware root via `$HERMES_HOME` | No | Yes |
| Curator activity/provenance/state analysis | No | Yes |
| Context-cost and large-skill review | No | Yes |
| Reference pointer, traversal, and symlink-escape validation | No | Yes |
| Approval-gated archive/restore workflow | No | Yes |

The tools are complementary, with one important compatibility caveat: `agent-skills-lint`'s Hermes flavor currently recognizes only `name`, `description`, and `trigger`. Standard Hermes metadata such as `version`, `author`, `license`, `platforms`, and `metadata` will be reported as unknown frontmatter keys (warnings, or errors in strict mode). Its Hermes install root is also hard-coded to `~/.hermes/skills`, so pass an explicit profile path when working outside the default profile.

Use it as a non-strict structural lint for a specific profile directory, not as a replacement for Hermes' native curator data or this skill's pointer validation:

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

The test suite covers local and cross-skill pointers, missing targets, path traversal, absolute pointer paths, profile-root resolution, symlink escape prevention, hidden-tree exclusion, and curator telemetry identity mapping.

## Safety model

- No automatic archive, deletion, or purge.
- Back up before approved structural changes.
- Prefer curator archive over destructive removal.
- Verify every pointer after restructuring.
- Treat zero use as evidence to inspect, not permission to remove.

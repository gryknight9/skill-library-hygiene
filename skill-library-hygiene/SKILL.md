---
name: skill-library-hygiene
description: "Audit local SKILL.md bodies for context bloat."
version: 1.0.0
author: gryknight9, Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [skills, context-window, maintenance]
    related_skills: []
---

# Skill Library Hygiene (personal library)

Context-cost auditing and restructuring of the **user-local** skill library at `~/.hermes/skills/`. Not for in-repo authoring conventions (that's a separate bundled concern).

## When to Use
- Context window is bloated and skills are suspected as the cause (`hermes prompt-size --json`, agent.log deferred-token lines)
- User asks why a routine task costs X tokens / wants the skill index shrunk
- Periodic library pass: restructure fat bodies, find unused skills, propose demotions
- Before deleting an existing skill (telemetry + pointer checks below)

Don't use for: writing new skills from scratch, or editing bundled/hub/pinned/user-owned skills — those need sign-off or `hermes curator adopt <name>`.

## Why it matters (cost model)
1. **Index tax:** every skill's name + ~57-char description ships in `<available_skills>` in EVERY session's system prompt, used or not. 120+ skills = permanent per-turn cost with no offsetting value on the ones never loaded.
2. **Body re-load:** matching a task loads the FULL SKILL.md into context. No caching between sessions or across compaction — each consultation re-pays the whole body. A 48KB skill loaded twice in one long session costs its tokens twice.

Both channels scale linearly with library size/fatness; neither has automatic cleanup. This skill exists because organic growth (lessons appended to bodies over months) silently fattens both.

## Core procedure: library audit

```python
# Measure: walk all SKILL.md, join usage telemetry, sort by size
import os, json
base=os.path.expanduser('~/.hermes/skills')
u=json.load(open(base+'.usage.json'))   # name -> {use_count/loads, last_used} shape varies; inspect first
rows=[]
for root,dirs,files in os.walk(base):
    if 'SKILL.md' in files:
        p=os.path.join(root,'SKILL.md')
        rel=os.path.relpath(p,base).replace('/SKILL.md','').replace('devops/devops/','')
        meta=u.get(rel,{})
        rows.append((os.path.getsize(p), meta.get('use_count',meta.get('loads',0)), str(meta.get('last_used','')), rel))
rows.sort(reverse=True); [print(f"{c:>7} {l:>4} {lu:<12} {n}") for c,l,lu,n in rows]
```

Bands of interest: >15k chars = restructure candidates; 9–15k = consider; zero loads + never used = demotion candidates (decision goes to the user — never disable/delete unilaterally).

## Restructure pattern (lean core + references)
Per skill, one batch at a time so progress survives compaction:
1. `cp SKILL.md SKILL.md.bak-<YYYYMMDD>` alongside.
2. New body ≤ ~10k chars keeping only: trigger conditions, cold environment facts (hostnames, ports, credential locations), safety rules / high-frequency pitfalls that cause real damage if missing, and a domain index table mapping work area → specific `references/<file>.md`.
3. Move out: command tables with worked examples, incident histories ("what happened last Tuesday"), API listings, edge-case catalogs, long troubleshooting trees. Stale one-offs → `references/archive/` inside the same skill. Nothing deleted without asking.
4. Verify: run `scripts/verify_ptrs.py <skill-dir>` per touched skill — prints local `references/*.md` pointer status + body char count (use the after-count for the before/after table). PITFALL: always run pointer checks from a script file like this — inline heredoc or execute_code snippets get their quoting corrupted by sandboxing and produce false failures. Cross-skill pointers must name **specific files** in the sibling's references dir — never "see skill X" (force-loads the whole sibling body). Zero dangling pointers before moving on, AND sweep sibling skills that point INTO the restructured skill (a rewrite can orphan an incoming cross-pointer) — flag every hit as a fix-or-repoint item in the progress file rather than silently deleting it.

Never append lessons to bodies — that is how skills rot from 8k to 48k. Rule-level lesson → patch into body; otherwise → relevant reference file.

## Multi-skill passes: compaction-safe method
A library-wide pass ALWAYS spans context compactions. Without external state you will redo or skip work after each compression boundary.
- Keep a running tally in a progress file OUTSIDE the skills tree (e.g. in your notes or wiki): `skill | before | after | notes`, updated after EVERY completed skill.
- Handoff prompt for continuation sessions MUST instruct: after any compaction, read the progress file first; do not redo finished skills.
- For large passes, prefer a fresh session started from a self-contained handoff prompt (state + rules + deliverable) over continuing a near-full context.

## Delete / demote discipline
Before removing or disabling an existing skill:
1. Usage telemetry (`.usage.json`) — loads, last-used date.
2. Grep all other SKILL.md bodies AND cron job configs (`~/.hermes/cron/jobs.json` prompts + skills lists) for pointers to it.
3. Zero loads + zero pointers = safe candidate; still confirm with the user. Agent-created deletion needs explicit sign-off.
4. Bundled/vendor product-y skills (claude-code, notion, box, airtable…): propose disable via profile `skills.disabled` as a decision item; never act silently.

## Verification
- Dangling-pointer check output shows zero unresolved across every touched skill.
- Per-skill before/after table written up; total recurring body-cost delta computed (chars × ~0.25 ≈ tokens).
- If you maintain an operations registry or wiki, record the pass there too; otherwise the progress file is the deliverable.
- Spot-check one restructured skill live (`skill_view`) — new body renders, index table matches real files.

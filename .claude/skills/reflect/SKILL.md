---
name: reflect
description: Learn from this session's interactions and propose improvements to the .claude/ config — which skill (or rule/agent/hook) should change, and how. Use when the user asks to capture a lesson or improve the setup, or proactively when you notice a recurring correction, friction, or a stated preference worth encoding. Always proposes; never edits config silently.
---

# reflect — turn interactions into config improvements

Close the loop between how the user actually works and what the `.claude/` config encodes. You
observe signals from the conversation, find the **single artifact** that should change to stop the
issue recurring, and **propose** a concrete edit. You never change config without showing the user.

## Cardinal rule
**Always tell the user before suggesting or applying a config change.** Surface what you learned,
which file you'd change, and the exact edit — then apply only after explicit approval. No silent
edits, ever. This holds even in autonomous/loop runs.

## 1. Gather signals (learn from the interaction)
Scan the session for moments that reveal a gap:
- **Corrections** — the user redirected you ("no, do X", "always Y", "don't Z").
- **Repetition** — you needed the same instruction twice, or repeated a mistake.
- **Friction** — a skill misfired, was missing, too broad/narrow, or didn't trigger when it should.
- **Stated preferences** — the user taught a convention, tool choice, or standard.
- **Guardrail noise** — a permission prompt or hook block that recurs and hints at a settings tweak.

For each, jot one line: *signal → what it implies.*

## 2. Route to the right artifact (most specific wins)
Map each lesson to the single best home:
- **Skill** (`skills/<name>/SKILL.md`) — the workflow's steps were wrong or missing.
- **Rule** (`rules/*.md`) — a standing convention/standard (path-scope it if stack-specific).
- **Agent** (`agents/*.md`) — a subagent's persona, scope, tools, or output contract was off.
- **Hook / settings** (`hooks/`, `settings.json`) — something that must be **enforced**, not advised.
- **CLAUDE.md** — a project-wide operating instruction.
- **Memory** (not config) — a one-off, person-specific fact → suggest saving to memory instead of
  bloating a skill/rule.

Prefer the smallest, most specific change. One lesson → one artifact. If a lesson is "this must
always hold," route it to a hook + permission (enforcement) rather than prose (guidance).

## 3. Propose (always)
Present each proposed change to the user:
- **Lesson** — the signal and why it will recur.
- **Target** — the exact file (note if it's the canonical copy in the ccc source).
- **Change** — the precise edit as a diff or before/after, kept minimal.
- **Why here** — the altitude/artifact choice (guidance vs enforcement; skill vs rule vs hook).

Then ask for approval. If the user declines or rewrites the proposal, follow that.

## 4. Apply (only after approval)
- ccc is the **source of truth.** If `.claude/.ccc-source` exists, make the edit in the canonical
  file there so it propagates, then remind the user to `/sync-config` other repos. Otherwise edit
  the local copy.
- Bump the ccc `VERSION` when you change shipped config.
- Keep the edit minimal and consistent with the file's existing style.

## Anti-scope
Don't refactor unrelated config. Don't encode transient, one-off context as a permanent rule. Don't
apply anything the user hasn't seen and approved. When unsure whether a lesson is durable, say so and
ask rather than guessing.

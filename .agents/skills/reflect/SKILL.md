---
name: reflect
description: Learn from the current interaction and propose a durable improvement to repository guidance, reusable skills, or user memory. Always proposes first and never changes shared guidance silently.
---

# reflect — turn interaction lessons into durable improvements

Use this skill when the user asks to capture a lesson or improve the agent setup, or when repeated
correction or friction reveals a likely process gap.

## Cardinal rule

Always show the user what was learned and the exact improvement being proposed before changing
shared repository guidance or skills. Apply it only after explicit approval.

A user may directly ask you to remember a personal fact or preference; in that case, save it to
memory rather than adding person-specific guidance to the repository.

## 1. Gather signals

Review the interaction for evidence that something should work differently next time:

- **Corrections:** The user redirected an approach or repeated an instruction.
- **Repetition:** The same clarification, mistake, or manual sequence occurred more than once.
- **Friction:** A skill was missing, stale, too broad, too narrow, or triggered poorly.
- **Standing preferences:** The user stated a durable convention or tool preference.
- **Guardrail gaps:** A recurring approval or safety issue suggests stronger enforcement outside
  prose documentation.

Summarize each candidate as `signal → lesson`. Do not turn one-off task details into permanent
configuration.

## 2. Choose the narrowest durable home

Route each lesson to the most specific appropriate location:

- **Reusable workflow:** `.agents/skills/<name>/SKILL.md`.
- **Repository-wide engineering standard:** `AGENTS.md`.
- **Provider compatibility pointer:** a small provider-specific guide such as `CLAUDE.md`, without
  duplicating the canonical instructions.
- **User-specific fact or preference:** durable memory, not a repository file.
- **Behavior that must be enforced:** application approval boundaries, validation, tests, CI, or
  another executable control—not prose alone.
- **Transient or uncertain lesson:** keep it out of durable configuration and explain why.

Prefer one lesson and one target at a time. Update an existing artifact when possible instead of
creating overlapping guidance.

## 3. Propose before editing

Present a concise proposal containing:

- **Lesson:** What happened and why it is likely to recur.
- **Target:** The exact file, skill, memory entry, or enforcement point.
- **Change:** The precise addition or revision, ideally as a short diff or before/after.
- **Why here:** Why this location and level of enforcement are appropriate.

Ask for approval. If the user declines or revises the proposal, follow that direction and do not
apply the original version.

## 4. Apply only the approved change

After approval:

- Make the smallest change that captures the lesson.
- Preserve existing style and avoid unrelated cleanup.
- Keep `AGENTS.md` canonical for shared repository guidance; provider-specific files should point
  to it rather than copy it.
- Keep skills portable and free of assumptions about one model provider or client unless that is
  explicitly the skill’s purpose.
- Add or update executable checks when the lesson concerns behavior that must always hold.
- Review the diff and run the relevant verification.

## 5. Report

State what changed, where the lesson now lives, and how it was verified. If the lesson was saved to
memory, say so plainly. If no durable update was appropriate, explain that instead of creating
configuration churn.

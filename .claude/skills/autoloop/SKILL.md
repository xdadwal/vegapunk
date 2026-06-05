---
name: autoloop
description: Run an autonomy-forward "ralph-style" loop — plan, act on one step, verify, review, repeat toward a goal until an explicit stop condition is hit. Use when the user wants Claude to drive a multi-step task semi-autonomously. Sets up the goal/specs scaffolding and enforces stop conditions so it never runs away.
---

# autoloop — autonomy-forward task loop

A disciplined loop for driving a task to completion with minimal supervision. It is **safe by
construction**: the repo's guardrail hooks (`.claude/hooks/`) block destructive actions and
secret edits even under `--dangerously-skip-permissions`, and the loop **must** define explicit
stop conditions before it starts.

> Pattern lineage: Geoffrey Huntley's "ralph loop" — a single process, one repo, **one task per
> iteration** toward a goal. This skill adds the goal/specs scaffolding, stop conditions, and a
> review gate. It composes with the installed `ralph-loop` plugin and the `verify` /
> `code-review` skills rather than replacing them.

## When to use
- A well-scoped, verifiable task with a clear "done" check (tests pass, a feature works, a
  migration is complete).
- NOT for security-critical changes, dependency-upgrade churn, or vague open-ended goals — those
  need a human in the loop (ralph loops are documented to underperform there).

## Step 1 — Define the contract (do this first, always)
Create/confirm two things in the repo:

1. **`PROMPT.md`** — the standing instruction the loop re-reads each iteration. Template:
   ```markdown
   # Goal
   <one concrete, verifiable outcome>

   # Acceptance check (how "done" is proven)
   <exact command(s) that must pass, e.g. `pnpm test && pnpm typecheck`>

   # Constraints
   - Smallest change per iteration; keep the build green.
   - Follow .claude/rules/. Do not touch <out-of-scope paths>.

   # Working notes (the loop appends here)
   ```
2. **`specs/`** (optional) — backing specifications/reference the task needs. Keep PROMPT.md
   short; put detail in `specs/*.md` and reference it.

## Step 2 — The iteration (repeat)
Each pass does exactly one unit of work:
1. **Orient**: re-read `PROMPT.md` + working notes. Delegate codebase questions to the
   `explorer` agent to keep context lean.
2. **Plan one step**: the single most valuable next action toward the goal.
3. **Act**: make that change. Small and reversible.
4. **Verify**: run the acceptance check (delegate to the `test-runner` agent). Red = fix before
   moving on; never advance on a broken build. If a failure's cause is unclear, delegate to the
   `debugger` agent for root-cause.
5. **Review**: for non-trivial changes, run the `reviewer` agent (or `/code-review`).
6. **Record**: append a one-line progress note (what changed, what's next) to `PROMPT.md`'s
   working notes so the next iteration has continuity.
7. **Check stop conditions** (below). If none met, loop.

## Step 3 — Stop conditions (mandatory; never "loop forever")
Stop and hand back to the user when ANY of these is true:
- ✅ **Done**: the acceptance check passes and no TODOs/`specs` items remain.
- 🔁 **No progress**: N consecutive iterations (default 3) with no measurable advance.
- ⛔ **Blocked**: a guard hook blocks an action, or an external dependency/decision is needed.
- 💸 **Budget**: a preset iteration or token budget is reached.
- ❓ **Ambiguity**: the goal turns out underspecified — stop and ask rather than guess.

Always end with a summary: what was accomplished, what's left, and the acceptance-check result.

## Step 4 — Running it unattended (optional)
For hands-off runs, the canonical external loop is:
```bash
while :; do cat PROMPT.md | claude -p ; done    # one task per iteration
```
Prefer the installed **`/ralph-loop`** command to manage this with logging and cancellation
(`/cancel-ralph` to stop). Before any unattended run:
- Work on a dedicated branch (never `main`), so everything is reviewable and revertible.
- Keep the guardrail hooks enabled — they are the safety floor.
- Require a **human review + merge gate**: the loop may commit to its branch but must not be the
  one to merge to `main`.

## Safety recap
Guidance (this skill, `.claude/rules/`) can be ignored; **hooks and permissions cannot**. That
asymmetry is what makes autonomy acceptable here — but it is not a substitute for reviewing the
diff before it ships.

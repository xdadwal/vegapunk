---
name: ship
description: Drive a change from idea to commit interactively — plan, implement, verify, review, then commit. Use for a normal human-in-the-loop feature or fix when you want the full disciplined pipeline in one go.
---

# ship — plan → implement → verify → review → commit

The interactive counterpart to `autoloop`: you stay in the loop, but the steps are enforced so
nothing gets skipped. Act as the **tech lead** — delegate each phase to the right subagent
(`explorer` / `test-runner` / `reviewer` / `debugger`) and synthesize. Reuse the installed
skills/agents rather than reimplementing them.

## Pipeline

1. **Understand & plan**
   - Clarify the goal and acceptance check. If anything material is ambiguous, ask now.
   - Use the `explorer` agent to locate the relevant code and existing patterns to reuse.
   - State a short plan (files to touch, approach) before writing code.

2. **Implement**
   - Make the smallest change that satisfies the goal, following `.claude/rules/`.
   - Add/update tests for the behavior you changed.

3. **Verify**
   - Run the relevant tests + type-check via the `test-runner` agent. Fix until green.
   - If a test fails and the cause isn't obvious, hand it to the `debugger` agent for root-cause.
   - For UI/behavioral changes, exercise the actual behavior (the `/verify` skill if available).

4. **Review**
   - Run the `reviewer` agent (or `/code-review`) over the diff. Address high/medium findings.
   - Re-run the acceptance check after fixes.

5. **Commit**
   - Only when verification is green. Branch first if on `main`/`master`.
   - Use a Conventional Commit (see `.claude/rules/git.md`). Stage intended files explicitly —
     not `git add -A`. Push prompts for confirmation; opening a PR is a separate, explicit step.
     (The `commit-commands` plugin's `/commit` or `/commit-push-pr` can do this.)

## Output
End with: what changed, how it was verified (command + result), and the commit/branch — stated
plainly. If you stopped before committing (red build, open question), say exactly why.

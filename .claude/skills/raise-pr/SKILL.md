---
name: raise-pr
description: Commit, push, and open a pull request for the current change, following this repo's git conventions (pre-flight checks, intentional staging, Conventional Commits, focused PR description). Use when the user asks to raise/open/create a PR, or to ship the current work to GitHub for review. Picks up where `ship` (which stops at commit) leaves off.
---

# raise-pr — commit, push, open a PR

Take the current change from working tree to an open pull request, without cutting the corners
that make a PR hard to review or unsafe to merge. Embodies `.claude/rules/git.md`. Only run this
when the user asks — never commit/push/PR unprompted.

## Step 1 — Pre-flight (don't skip any)

- **Green build gates everything.** Run the relevant tests + type-check/build (delegate to the
  `test-runner` agent). Red build → stop and report; never raise a PR over failing tests.
- **Review the diff.** For non-trivial changes, run the `reviewer` agent (or `/code-review`) on the
  working-tree diff and address material findings *before* committing.
- **Be on a feature branch, not `main`/`master`.** If on the default branch, branch first:
  `feat/<short-desc>` (or `fix/`, `chore/`, `docs/`). `git branch --show-current` to check.
- **Sync the base.** `git fetch origin`; if `origin/<base>` has moved past your branch point,
  rebase onto it and resolve conflicts deliberately (never blind-accept one side).
- **Confirm tooling.** `gh auth status` succeeds; note the remote (`git remote -v`) and the base
  branch (usually `main`/`master`).

## Step 2 — Refresh the README

- **Keep `README.md` true to this change.** Before staging, run the `/update-readme` skill so the
  README reflects anything reader-facing this PR changes — a tool added/removed, a CLI command or
  flag, run/setup steps, config or environment variables, dependencies, or the project layout.
- It makes the **smallest faithful edit**, and **no edit at all** when nothing reader-facing changed
  (internal refactor, tests-only). Don't force a README change just to satisfy this step.
- The README edit is part of *this* PR's diff: stage it with the rest in Step 3 and mention it in the
  commit/PR body.

## Step 3 — Stage intentionally

- **Never `git add -A`.** Stage only the files this change touches, by explicit path
  (`git add <paths>`).
- **Exclude what isn't part of the change**: scratch/scaffolding files (e.g. `PROMPT.md`), local
  test artifacts, generated output, anything you didn't create for this work. Then run
  `git status --short` and confirm *nothing unexpected* is staged.
- **Never stage secrets** (`.env`, tokens, keys — hooks block `.env`, but look), large binaries, or
  lockfiles you hand-edited (regenerate those instead).

## Step 4 — Commit

- **Conventional Commits**: `type(scope): summary` in the imperative, ≤ ~72 chars
  (`feat`/`fix`/`refactor`/`test`/`docs`/`chore`/`perf`/`build`/`ci`).
- **One logical change per PR.** If the work is genuinely several changes, say so and offer to split
  (separate commits, or separate branches/PRs) rather than burying them.
- The **body explains _why_** and **how it was verified**, not just what (the diff shows what).
- **End the commit message with the session's `Co-Authored-By:` trailer** (the harness specifies the
  exact line for the current model).

## Step 5 — Push

- `git push -u origin <branch>`. Pushing prompts for confirmation (configured in settings) — that's
  expected.
- Force-push with `-f`/`--force` is blocked by a hook; if you must, use `--force-with-lease`, and
  never on a shared branch.

## Step 6 — Open the PR

- `gh pr create --base <main|master> --head <branch> --title "<conventional title>" --body "<body>"`.
- **Title** carries the full scope (the branch name may be narrower — if it materially misleads,
  offer to rename the branch and re-point the PR).
- **Body is focused**: _what_ changed and _why_, plus _how it was verified_ (tests, manual run).
  No unrelated changes. End the body with:

  ```
  🤖 Generated with [Claude Code](https://claude.com/claude-code)
  ```

## Step 7 — Report

Print the PR URL `gh` returns, and a one-line summary of what landed (commit count, files, test
result). Offer follow-ups only if relevant (reviewers/labels, branch rename, splitting commits).

## Notes

- This is the GitHub-facing tail of the pipeline: `ship`/`autoloop` get you to a verified, committed
  change; `raise-pr` takes that to a reviewable PR.
- If nothing is committed yet, this skill does the commit too (Steps 3–4). If the change is already
  committed, start at Step 5.
- Keep the human as the merge gate — open the PR; don't merge it unless explicitly asked.

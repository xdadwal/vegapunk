---
name: update-readme
description: Update README.md so it stays true to the codebase — what Vegapunk is, how to run it, its tools, configuration/env vars, and setup. Runs automatically as a step in `raise-pr` (refresh the README before opening a PR) and on demand when you ask to update/refresh the README. Makes the smallest faithful edit, or none if nothing reader-facing changed.
---

# update-readme — keep README.md true to the code

`README.md` is the front door. It must describe Vegapunk **as it actually is** — never a feature,
command, flag, env var, or dependency that isn't in the code. This skill refreshes it from the
source of truth, making the smallest faithful edit (or none). It runs as a step in `raise-pr` so a
PR never ships a README that lies, and you can also run it on demand.

Treat the README with the same honesty rules as code (`.claude/rules/fundamentals.md`): no
fabricated claims, no aspirational features dressed up as real ones.

## Step 1 — Decide whether an update is even needed

Look at what this change touches (`git diff <base>...HEAD`, or the working tree if uncommitted).
Ask: does it change anything a **reader** of the README would care about?

- a tool added / removed / renamed, or a change to what one does
- a CLI command, subcommand, or flag; how you start, drive, or stop it
- install/setup steps, the Python version, or dependencies
- config or `VEGAPUNK_*` environment variables, their defaults, the model/runtime
- the project layout, or what the thing fundamentally is

If nothing reader-facing changed (internal refactor, tests-only, a private helper), **make no edit**
and say so. Don't churn the README to look busy — an unnecessary edit is noise in the diff.

## Step 2 — Ground every claim in the source of truth

Read the current `README.md`, then read what defines reality — don't write from memory:

- **What it is / persona / model**: `vegapunk/config.py` (system prompt, `VEGAPUNK_*` defaults, the
  Docker Model Runner base URL + model id) and `vegapunk/brain.py`.
- **How to run it**: `vegapunk/__main__.py` and `vegapunk/cli.py` (the `.venv/bin/python -m vegapunk`
  REPL — note the `exit`/`reset` commands), plus the `try_agent.py` / `try_brain.py` demos.
- **Capabilities**: the `@tool` modules in `vegapunk/tools/` and what's actually wired into
  `ALL_TOOLS` in `vegapunk/tools/__init__.py`. Note which tools are gated behind approval
  (`vegapunk/approval.py`) — that's reader-relevant.
- **Install / deps**: `requirements.txt`, `requirements-dev.txt`.
- **Tests**: `pytest.ini`, `tests/`.

If a file moved or a fact is unclear, search for it — don't assume the anchors above are still exact.

## Step 3 — Write only what's true, in the smallest faithful diff

- **Every command, flag, env var, tool, and dependency you mention must exist in the code.** When in
  doubt, verify (run it / grep the symbol) rather than guess.
- **Edit the sections the change affects**; don't rewrite the whole file for a one-line change. Match
  the existing tone and structure.
- If the README is still a stub, it's fine to establish the initial shape — but keep it to what's
  real: *what Vegapunk is · how to run it · its tools · configuration (`VEGAPUNK_*`) · running the
  tests*. Use the repo's own commands (`.venv/bin/python -m vegapunk`, `.venv/bin/python -m pytest
  -q`).
- No invented badges, license, screenshots, or roadmap unless they already exist or the user asks.

## Step 4 — Verify the instructions actually work

Documentation is a claim; check it like one. For anything runnable you add or change:

- the launch/run/test commands do what the README says (e.g. `.venv/bin/python -m pytest -q` passes;
  `.venv/bin/python -m vegapunk` is the real entry point per `__main__.py`).
- env-var names and defaults match `config.py` exactly; tool names match the `@tool` functions.

Reading the code is the minimum; run the command when it's cheap to confirm.

## Step 5 — Report

Say which sections you changed and why — or state plainly that the README already reflected the
change and you edited nothing.

## Notes

- **Staging is the caller's job.** This skill only edits `README.md` in the working tree. Inside
  `raise-pr` the README edit is part of *this* PR's diff, so it gets staged and mentioned with the
  rest of the change. Run standalone, it leaves staging/committing to you.
- It pairs with `add-tool`: when you add a capability, the tool list and any new `VEGAPUNK_*` setting
  are exactly the kind of reader-facing change this skill should pick up.

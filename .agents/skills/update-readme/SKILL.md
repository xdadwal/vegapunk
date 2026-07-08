---
name: update-readme
description: Update README.md so it stays true to the codebase — what the project is, how to run it, its command…
---

# update-readme — keep README.md true to the code

`README.md` is the front door. It must describe the project **as it actually is** — never a
feature, command, flag, env var, or dependency that isn't in the code. This skill refreshes it
from the source of truth, making the smallest faithful edit (or none). It runs as a step in
`raise-pr` so a PR never ships a README that lies, and you can also run it on demand.

Treat the README with the same honesty rules as code: no fabricated claims, no aspirational features dressed up as real ones.

## Step 1 — Decide whether an update is even needed

Look at what this change touches (the working tree). Ask: does it change anything a **reader** of the README would care about?

- a command, subcommand, flag, tool, or capability added / removed / renamed
- how you install, configure, start, drive, or stop it
- setup steps, the runtime/language version, or dependencies
- configuration or environment variables, and their defaults
- the project layout, or what the thing fundamentally is

If nothing reader-facing changed (internal refactor, tests-only, a private helper), **make no
edit** and say so. Don't churn the README to look busy — an unnecessary edit is noise in the diff.

## Step 2 — Ground every claim in the source of truth

Read the current `README.md`, then read what defines reality for **this repo's stack**. Detect the stack (Python/Node/etc.) and go to its real anchors:

- **What it is / entry point / how to run it** — the main entry/CLI and the run scripts.
  - Python: `pyproject.toml`/`setup.cfg` (entry points/scripts), `__main__.py`, the CLI module.
- **Configuration & env vars** — the config/settings module and any `.env.example`.
- **Capabilities / commands / tools** — where they're defined and which are actually wired in (the
  registry/index), plus anything gated behind approval or a flag (reader-relevant).
- **Install & dependencies** — `pyproject.toml`/`requirements*.txt`.
- **Tests** — the test config and directory (e.g. `pytest.ini`/`pyproject`).

If a file moved or a fact is unclear, search for it — don't assume an anchor is where you expect.

## Step 3 — Write only what's true, in the smallest faithful diff

- **Every command, flag, env var, tool, and dependency you mention must exist in the code.** When
  in doubt, verify (run it / grep the symbol) rather than guess.
- **Edit only the sections the change affects**; don't rewrite the whole file for a one-line change.
  Match the existing tone and structure.
- If the README is still a stub, it's fine to establish the initial shape — but keep it to what's
  real: *what the project is · how to run it · its commands/tools · configuration · running the
  tests*, using the repo's own commands.
- No invented badges, license, screenshots, or roadmap unless they already exist or the user asks.

## Step 4 — Verify the instructions actually work

Documentation is a claim; check it like one. For anything runnable you add or change:

- the install/run/test commands do what the README says — run the repo's real test and entry-point
  commands when it's cheap to confirm, rather than assuming.
- env-var names and defaults match the config source exactly; command/tool names match their
  definitions in code.

Reading the code is the minimum; run the command when it's cheap to confirm.

## Step 5 — Report

Say which sections you changed and why — or state plainly that the README already reflected the
change and you edited nothing.

## Notes

- **Staging is the caller's job.** This skill only edits `README.md` in the working tree. Inside
  `raise-pr` the README edit is part of *this* PR's diff, so it gets staged and mentioned with the
  rest of the change. Run standalone, it leaves staging/committing to you.
- When you add a capability (a new command, tool, or config option), that's exactly the kind of
  reader-facing change this skill should pick up.

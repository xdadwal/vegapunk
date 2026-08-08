---
name: verify-change
description: Run the narrowest relevant tests and checks for a change, summarize failures concisely, and assess whether changed behavior is covered. Use after implementation, before committing, or when an independent verification result is needed.
---

# verify-change — run checks and return a compact verdict

Verify the change using the repository’s real commands while keeping verbose logs out of the main
working context.

## Delegation

If the host exposes native subagents, delegate verification to a fresh subagent with permission to
read files and run non-destructive checks, but not edit code. Provide the changed scope and output
contract below. Wait for the result and preserve the exact command and exit status in the summary.

If delegation is unavailable, run the workflow in the current agent. The skill must remain useful
without a subagent system; never install one automatically.

## Process

1. Read `AGENTS.md` and detect check commands from project configuration, scripts, CI, and nearby
   test files rather than guessing.
2. While iterating, run the narrowest suite that exercises the change.
3. Before completion, run the repository’s required full checks when practical.
4. Capture command, exit status, pass/fail counts, and the shortest useful failure signal.
5. Compare changed behavior with existing tests and identify meaningful uncovered paths.

For this repository, the standard full suite is:

```bash
.venv/bin/python -m pytest -q
```

Use narrower pytest targets first when they can shorten diagnosis, but do not present a narrow pass
as proof that the full required suite passed.

## Output contract

- **Verdict:** PASS or FAIL and the exact command.
- **Counts:** Passed, failed, skipped, or unavailable.
- **Failures:** `test — file:line — key error`, without full logs.
- **Likely cause:** One sentence per distinct failure when supported by evidence.
- **Coverage note:** Whether a regression in the changed behavior would be caught.
- **Skipped checks:** Anything required but not run, with the reason.

## Constraints

Diagnose and report; do not edit implementation or tests. Do not omit a red check, weaken a test,
or claim full verification from a partial run.

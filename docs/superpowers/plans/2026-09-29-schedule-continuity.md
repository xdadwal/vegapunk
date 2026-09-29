# Scheduled-run continuity

Continuation of the approved Moltbook maturity plan, Stage 2. This PR delivers
run continuity; mutation receipts and scoped publishing remain a subsequent slice.

## Design

Keep historical runs separate from scheduled task definitions. Persist a running
record before invoking a provider; refuse execution if that write fails or another
run is open. Store bounded tool metadata as events arrive, without arguments or
raw response bodies. Atomically finish the run and advance the task. Preserve
history when a task is removed. Only the worker holding the scheduler lock may
recover orphaned running rows on startup, marking them interrupted and delaying
their next attempt by one interval.

Classify operational evidence independently from model prose: completed means a
turn ended without verified success; generic tool strings count only as returned.
Success means a Moltbook read returned its client's success envelope; blocked means
all observed attempts were blocked; partial means mixed success/failure; error
means an exception, tool error, or step limit. These do not certify that the
user's objective was met. Moltbook's read client has a known success envelope;
failed authenticated reads must not be called successful just because they return
a string. No new write capability is introduced.

Inject at most three previous summaries (500 characters each), scoped to the
current task, as explicitly untrusted historical data. Expose recent runs and
their tool outcomes through `/schedule history [task-id]`, including removed
tasks. Persist full bounded summaries (2000 characters) for inspection.

## Implementation

- [x] Add failing tests for history, blocked/partial outcomes, isolation, startup
  recovery, persistence failures, and the command.
- [x] Add schema v6 tables and `task_history.py` for begin, event, finish,
  recovery, bounded reads and context. Use existing immediate transactions.
- [x] Observe loop events through an optional callback; integrate the runner and
  locked worker startup. Keep the legacy `record_run` interface working.
- [x] Add the command and update README and Moltbook skill.
- [ ] Run focused then full tests, independent review, inspect the diff, sync the
  base, commit, push and open a PR.

## Verification focus

Test reopened databases and v5 upgrades, interruption before completion, a failed
history write preventing the provider from running, mixed tool outcomes, removal
without loss of evidence, and isolation between two tasks. No live account
mutation or scheduler reconfiguration is required for this change.

Validation: full suite passed with 899 tests; independent final review found no
material findings. Abrupt process exit, competing starts, atomic rollback, v5
upgrade, backup recovery, context bounds, and generic error-string handling have
regression coverage. Live scheduler deployment remains outside this PR.

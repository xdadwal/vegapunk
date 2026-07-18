"""Scheduled tasks — saved prompts Vegapunk runs itself, on an interval.

A scheduled task is just a *prompt* plus a repeat interval: "fetch this page and
remember what changed" is a prompt that calls fetch_url then remember, run every
N seconds by the REPL's background ticker rather than typed by a human. This
module owns the ``scheduled_tasks`` table the same way ``memory`` owns ``memory``
— every query to that table lives here, so the SQL and the row shape stay in one
place.

Timing rides on ``db.utcnow()``'s fixed-width, lexicographically sortable stamp:
a task is *due* when its ``next_run_at`` is ``<=`` now, so "what's due" is a
string comparison in SQL with no datetime parsing. The runner advances
``next_run_at`` by one interval after each run (see ``record_run``, added with the
runner).

Everything here is best-effort against the database, mirroring ``memory``: a
``StoreError`` degrades to a stderr note and an empty/failure result rather than
crashing the REPL or the background thread.
"""

from __future__ import annotations

import sys
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from . import db

if TYPE_CHECKING:
    from .brain import Brain
    from .tools.base import Tool

_HEX = frozenset("0123456789abcdef")

# Every column, in table order — shared by the readers so the SELECT list and
# ``_row``'s unpacking can never drift apart.
_COLUMNS = (
    "id, prompt, interval_seconds, next_run_at, "
    "last_run_at, last_status, last_result, enabled, created_at"
)

# Cap on a run's stored result. ``last_result`` is overwritten each run and read
# for a status glance in ``/schedule list``, not kept as a full transcript, so a
# chatty reply is trimmed rather than stored whole — generous enough for a short
# summary, bounded so one run can't bloat the row.
_RESULT_CAP = 2000


@dataclass(frozen=True)
class ScheduledTask:
    """One row of ``scheduled_tasks``. ``enabled`` is surfaced as a bool though
    it's stored as SQLite's 0/1; timestamps are ``db.utcnow()`` strings (UTC),
    with the ``last_*`` fields None until the task has run at least once."""

    id: str
    prompt: str
    interval_seconds: int
    next_run_at: str
    last_run_at: str | None
    last_status: str | None
    last_result: str | None
    enabled: bool
    created_at: str


def _row(r: tuple) -> ScheduledTask:
    """Build a ScheduledTask from a ``_COLUMNS``-ordered row."""
    return ScheduledTask(
        id=r[0],
        prompt=r[1],
        interval_seconds=r[2],
        next_run_at=r[3],
        last_run_at=r[4],
        last_status=r[5],
        last_result=r[6],
        enabled=bool(r[7]),
        created_at=r[8],
    )


def add_task(prompt: str, interval_seconds: int) -> str:
    """Schedule ``prompt`` to run every ``interval_seconds``.

    The first run lands one interval from now (not immediately), so creating a
    task never fires a model turn inline — important once the model itself can
    create tasks mid-turn. Returns a confirmation naming the new short id, or a
    usage note for an empty prompt or a non-positive interval. This is the
    ``schedule_task`` tool's and ``/schedule add``'s result string.
    """
    prompt = prompt.strip()
    if not prompt:
        return "Nothing to schedule — the prompt was empty."
    if interval_seconds <= 0:
        return "Interval must be a positive number of seconds."
    now = db.utcnow()
    task_id = db.new_id()
    try:
        db.execute(
            "INSERT INTO scheduled_tasks "
            "(id, prompt, interval_seconds, next_run_at, enabled, created_at) "
            "VALUES (?, ?, ?, ?, 1, ?)",
            (task_id, prompt, interval_seconds, db.utcnow_plus(interval_seconds), now),
        )
    except db.StoreError as exc:
        return f"Could not schedule the task: {exc}"
    return f"Scheduled task {task_id[:8]} — runs every {interval_seconds}s (first run in {interval_seconds}s)."


def list_tasks() -> list[ScheduledTask]:
    """Every scheduled task, oldest first. ``[]`` (with a stderr note) on a
    database error."""
    try:
        rows = db.query(f"SELECT {_COLUMNS} FROM scheduled_tasks ORDER BY created_at, id")
    except db.StoreError as exc:
        print(f"  [scheduler] could not list: {exc}", file=sys.stderr)
        return []
    return [_row(r) for r in rows]


def remove_task(id_prefix: str) -> str:
    """Delete the one task whose id starts with ``id_prefix`` (git-style short id).

    Mirrors ``memory.forget_memory``'s outcomes: a usage note for a non-hex/empty
    prefix, a not-found note, an ambiguity note when more than one matches, or a
    confirmation naming the removed task.
    """
    prefix = id_prefix.strip().lower()
    if not prefix or any(c not in _HEX for c in prefix):
        return "Usage: /schedule remove <id>  (id is the short hex shown by /schedule list)"
    try:
        rows = db.query("SELECT id FROM scheduled_tasks WHERE id LIKE ? || '%'", (prefix,))
    except db.StoreError as exc:
        return f"Could not remove: {exc}"
    if not rows:
        return f"No scheduled task matches '{prefix}'."
    if len(rows) > 1:
        return f"'{prefix}' is ambiguous — matches {len(rows)} tasks; use more characters."
    task_id = rows[0][0]
    try:
        db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
    except db.StoreError as exc:
        return f"Could not remove: {exc}"
    return f"Removed scheduled task {task_id[:8]}."


def due_tasks(now: str | None = None) -> list[ScheduledTask]:
    """Enabled tasks whose ``next_run_at`` has arrived (``<= now``), oldest-due
    first.

    ``now`` defaults to ``db.utcnow()``; it's a parameter so the ticker and tests
    can drive a fixed clock. Returns ``[]`` (with a stderr note) on a database
    error — a failed read must not take the background thread down.
    """
    stamp = now if now is not None else db.utcnow()
    try:
        rows = db.query(
            f"SELECT {_COLUMNS} FROM scheduled_tasks "
            "WHERE enabled = 1 AND next_run_at <= ? ORDER BY next_run_at, id",
            (stamp,),
        )
    except db.StoreError as exc:
        print(f"  [scheduler] could not read due tasks: {exc}", file=sys.stderr)
        return []
    return [_row(r) for r in rows]


def run_task(task: ScheduledTask, brain: Brain, tools: list[Tool]) -> str:
    """Run one due task's prompt to completion and record the outcome.

    The prompt runs through the ordinary agent loop with **no approver**, which
    is fail-closed by construction (see ``loop._run_tool_batch``): read-only
    tools like ``fetch_url``/``search_web``/``recall``/``remember`` run
    unattended, while guarded tools (``write_file``/``run_shell``) are
    auto-blocked because no human is present to approve them. So a polling task
    that fetches a page and remembers a fact runs fully; one that tries to write
    the workspace is told it can't in this context and reports that back.

    Returns the run's result string (also stored on the row via ``record_run``).
    A failure inside the loop is caught here and recorded as an ``"error"`` run
    rather than raised, mirroring ``loop._run_tool``'s boundary posture: the
    ticker runs unattended, so one bad task must not take the thread — or its
    sibling tasks — down. ``KeyboardInterrupt`` is *not* caught (it's not an
    ``Exception``), so a Ctrl-C still propagates out to stop the process.
    """
    from . import loop  # lazy: avoids a scheduler <-> loop <-> tools import cycle

    try:
        result = loop.run(brain, tools, task.prompt, approver=None)  # approver=None => fail-closed
        status = "ok"
    except Exception as exc:  # noqa: BLE001 — boundary: an unattended run must not crash the thread
        result = f"Error running scheduled task: {exc}"
        status = "error"
    record_run(task, status, result)
    return result


def record_run(task: ScheduledTask, status: str, result: str, now: str | None = None) -> None:
    """Record one run's outcome on the row and advance the schedule.

    Stamps ``last_run_at``/``last_status``/``last_result`` (the result trimmed to
    ``_RESULT_CAP``) and pushes ``next_run_at`` one interval into the future
    *from now* — not from the old due time — so a task that ran late (or after the
    REPL was closed a while) simply runs again one interval later instead of
    firing a backlog of catch-up runs. ``now`` defaults to ``db.utcnow()`` and is
    a parameter for the same fixed-clock testability as ``due_tasks``.

    Best-effort: a ``StoreError`` degrades to a stderr note rather than raising,
    so a failed bookkeeping write never takes the background thread down. (The
    task keeps its old ``next_run_at`` and is simply retried on a later tick.)
    """
    stamp = now if now is not None else db.utcnow()
    trimmed = result if len(result) <= _RESULT_CAP else result[:_RESULT_CAP] + "…[truncated]"
    try:
        db.execute(
            "UPDATE scheduled_tasks SET last_run_at = ?, last_status = ?, "
            "last_result = ?, next_run_at = ? WHERE id = ?",
            (stamp, status, trimmed, db.stamp_plus(stamp, task.interval_seconds), task.id),
        )
    except db.StoreError as exc:
        print(f"  [scheduler] could not record run for {task.id[:8]}: {exc}", file=sys.stderr)


# How often the background ticker wakes to look for due tasks, in seconds. This
# is the poll cadence, not a task's own interval: a task set to every 300s still
# runs ~300s apart; this only bounds how soon after coming due it's noticed.
_DEFAULT_POLL_SECONDS = 30.0


class Scheduler:
    """The REPL's background ticker: wakes on a fixed cadence and runs whatever
    scheduled tasks have come due, in the same process as the interactive session.

    Serialization is the whole point of the shared ``lock``. Vegapunk runs one
    model and one Turso connection; a background task turn and a foreground
    (user-typed) turn must never touch either at the same time. The CLI guards
    its own ``session.send`` with the very lock it hands here, so the two turn
    kinds take strict turns: a due task that starts running makes a waiting user
    turn block until it finishes, and vice versa. The lock is released between
    tasks, so a user turn only ever waits out the single task in flight — not the
    whole due batch.

    The ticker is off until ``start`` and stops cleanly on ``stop`` (or, as a
    daemon thread, when the process exits regardless). ``run_due_now`` is the
    unit the ticker repeats, and is public so a test can drive it directly —
    without the thread — for a deterministic check.
    """

    def __init__(
        self,
        brain_provider: Callable[[], Brain],
        tools: list[Tool],
        lock: threading.Lock,
        poll_seconds: float = _DEFAULT_POLL_SECONDS,
    ) -> None:
        # A provider, not a snapshot: /model swaps the session's brain mid-run,
        # and a background task must follow that swap rather than run forever on
        # the launch model. Called under the lock at each task's run time, so it
        # reads the current brain the same instant a foreground turn would.
        self._brain_provider = brain_provider
        self._tools = tools
        self._lock = lock
        self._poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the background ticker. Idempotent — a second call while already
        running is a no-op, so the REPL can call it without tracking state."""
        if self._thread is not None:
            return
        self._stop.clear()  # allow a restart after a previous stop()
        self._thread = threading.Thread(
            target=self._loop, name="vegapunk-scheduler", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the ticker to stop and wait up to ``timeout`` for it to finish.

        Prompt when the thread is idle (parked on the poll) — the stop event wakes
        it at once. If a task is mid-run the join waits up to ``timeout``; a task
        that outlasts it is left to the daemon-thread reaper at interpreter exit
        rather than hanging the REPL's shutdown. Idempotent — a no-op if never
        started.
        """
        if self._thread is None:
            return
        self._stop.set()
        self._thread.join(timeout)
        self._thread = None

    def _loop(self) -> None:
        """Wait one poll interval, run due tasks, repeat until stopped.

        Waits *before* the first tick, so nothing fires the instant the REPL
        launches. A ``run_due_now`` that raises unexpectedly is logged and the
        loop continues: a background ticker must not die silently and leave the
        session with no further scheduled runs and no hint why.
        """
        while not self._stop.wait(self._poll_seconds):
            try:
                self.run_due_now()
            except Exception as exc:  # noqa: BLE001 — the ticker must outlive one bad tick
                print(f"  [scheduler] tick failed: {exc}", file=sys.stderr)

    def run_due_now(self) -> None:
        """Run every currently-due task once, each under the shared lock.

        Polls due tasks under the lock (the Turso connection is shared with the
        foreground), then runs each under the lock too, releasing between tasks so
        a waiting user turn can interleave. Bails out early once a stop has been
        signaled, so shutdown never starts a fresh task turn.
        """
        with self._lock:
            due = due_tasks()
        for task in due:
            if self._stop.is_set():
                return
            with self._lock:
                # Read the live brain under the lock, the same instant a
                # foreground turn would — so a /model swap is honored, not
                # a launch-time snapshot.
                run_task(task, self._brain_provider(), self._tools)

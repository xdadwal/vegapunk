"""Tests for the scheduled-tasks data layer — the DB-backed store in scheduler.py.

Scheduled tasks live in the embedded database; the autouse ``_isolated_vegapunk_home``
fixture (conftest) points ``db.db_path`` at a per-test tmp file, so these tests never
touch the developer's real ``.vegapunk/``. Timing is driven by explicit ``now`` stamps
rather than the wall clock, so the due-query tests stay deterministic.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest

from vegapunk import db
from vegapunk.brain import Brain, BrainResponse, TextDelta, ThinkEvent, ToolCall
from vegapunk.scheduler import (
    Scheduler,
    add_task,
    due_tasks,
    list_tasks,
    record_run,
    remove_task,
    run_task,
)
from vegapunk.tools.base import Tool


def _insert_task(
    task_id: str,
    prompt: str,
    next_run_at: str,
    *,
    interval_seconds: int = 60,
    enabled: int = 1,
    created_at: str = "2026-01-01T00:00:00.000000Z",
) -> None:
    """Insert a task row with fully controlled timing — lets the due-query tests
    pin next_run_at/enabled without leaning on the wall clock."""
    db.execute(
        "INSERT INTO scheduled_tasks "
        "(id, prompt, interval_seconds, next_run_at, enabled, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, prompt, interval_seconds, next_run_at, enabled, created_at),
    )


# --- add_task ---


def test_add_task_creates_row_with_future_first_run():
    result = add_task("poll example.com and remember changes", 300)

    assert "Scheduled task" in result
    tasks = list_tasks()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.prompt == "poll example.com and remember changes"
    assert task.interval_seconds == 300
    assert task.enabled is True
    # First run lands one interval out, never inline: next_run_at is after created_at.
    assert task.next_run_at > task.created_at
    assert task.last_run_at is None
    assert task.last_status is None
    assert task.last_result is None


def test_add_task_confirmation_names_short_id():
    result = add_task("do a thing", 60)
    task = list_tasks()[0]
    assert task.id[:8] in result


def test_add_task_empty_prompt_is_noop():
    result = add_task("   ", 60)  # whitespace only
    assert "Nothing to schedule" in result
    assert list_tasks() == []  # no row written


def test_add_task_rejects_non_positive_interval():
    assert "positive" in add_task("something", 0)
    assert "positive" in add_task("something", -5)
    assert list_tasks() == []  # nothing written on either rejection


def test_add_task_degrades_when_db_unavailable(monkeypatch):
    def _boom(*args, **kwargs):
        raise db.StoreError("db is toast")

    monkeypatch.setattr("vegapunk.db.execute", _boom)
    result = add_task("something", 60)
    assert "Could not schedule" in result  # error surfaced, not raised


# --- list_tasks ---


def test_list_tasks_empty_when_none_scheduled():
    assert list_tasks() == []


def test_list_tasks_returns_typed_rows_oldest_first():
    _insert_task("a" * 32, "first", "2026-02-01T00:00:00.000000Z", created_at="2026-01-01T00:00:00.000000Z")
    _insert_task("b" * 32, "second", "2026-02-01T00:00:00.000000Z", created_at="2026-01-02T00:00:00.000000Z")

    tasks = list_tasks()
    assert [t.prompt for t in tasks] == ["first", "second"]  # oldest created first
    assert all(len(t.id) == 32 for t in tasks)
    assert all(t.enabled is True for t in tasks)


def test_list_tasks_degrades_when_db_unavailable(monkeypatch, capsys):
    def _boom(*args, **kwargs):
        raise db.StoreError("db is toast")

    monkeypatch.setattr("vegapunk.db.query", _boom)
    assert list_tasks() == []
    assert "could not list" in capsys.readouterr().err


# --- remove_task ---


def test_remove_task_by_unique_prefix():
    add_task("keeper", 60)
    add_task("goner", 60)
    goner = next(t for t in list_tasks() if t.prompt == "goner")

    result = remove_task(goner.id[:8])
    assert "Removed" in result and goner.id[:8] in result
    assert [t.prompt for t in list_tasks()] == ["keeper"]


def test_remove_task_ambiguous_prefix_refuses(monkeypatch):
    # Two rows sharing a prefix — force it by stubbing the lookup (mirrors memory).
    def _two(sql, params=()):
        return [("id_aaa",), ("id_aab",)]

    monkeypatch.setattr("vegapunk.db.query", _two)
    result = remove_task("aa")
    assert "ambiguous" in result and "2 tasks" in result


def test_remove_task_unknown_prefix():
    assert "No scheduled task matches" in remove_task("deadbeef")


def test_remove_task_rejects_non_hex():
    assert remove_task("not-hex!").startswith("Usage:")


# --- due_tasks ---


def test_due_tasks_returns_only_enabled_and_due():
    _insert_task("a" * 32, "due", "2026-01-01T00:00:00.000000Z")
    _insert_task("b" * 32, "future", "2999-01-01T00:00:00.000000Z")
    _insert_task("c" * 32, "disabled-but-due", "2026-01-01T00:00:00.000000Z", enabled=0)

    due = due_tasks(now="2026-06-01T00:00:00.000000Z")
    assert [t.prompt for t in due] == ["due"]  # future not yet, disabled excluded


def test_due_tasks_orders_by_next_run_at():
    _insert_task("a" * 32, "later", "2026-05-01T00:00:00.000000Z")
    _insert_task("b" * 32, "earlier", "2026-03-01T00:00:00.000000Z")

    due = due_tasks(now="2999-01-01T00:00:00.000000Z")
    assert [t.prompt for t in due] == ["earlier", "later"]  # oldest-due first


def test_due_tasks_boundary_is_inclusive():
    _insert_task("a" * 32, "exactly now", "2026-06-01T00:00:00.000000Z")
    # next_run_at == now counts as due (<=).
    assert [t.prompt for t in due_tasks(now="2026-06-01T00:00:00.000000Z")] == ["exactly now"]
    # one microsecond before — not yet due.
    assert due_tasks(now="2026-05-31T23:59:59.999999Z") == []


def test_due_tasks_defaults_now_to_utcnow():
    # A task overdue in the past is picked up when now defaults to the wall clock.
    _insert_task("a" * 32, "due", "2000-01-01T00:00:00.000000Z")
    assert [t.prompt for t in due_tasks()] == ["due"]


def test_due_tasks_degrades_when_db_unavailable(monkeypatch, capsys):
    def _boom(*args, **kwargs):
        raise db.StoreError("db is toast")

    monkeypatch.setattr("vegapunk.db.query", _boom)
    assert due_tasks(now="2026-06-01T00:00:00.000000Z") == []
    assert "could not read due tasks" in capsys.readouterr().err


# --- the runner (run_task / record_run) ---


class _ScriptedBrain(Brain):
    """Plays back one scripted event stream per think() call (mirrors test_loop)."""

    def __init__(self, scripts: list[list[ThinkEvent]]) -> None:
        self._scripts = list(scripts)

    def think(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[ThinkEvent]:
        yield from self._scripts.pop(0)


def _response(text=None, tool_calls=None) -> BrainResponse:
    message: dict = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": "{}"}}
            for c in tool_calls
        ]
    return BrainResponse(message=message, text=text, tool_calls=tool_calls or [])


def _runner_tool(name, func, *, guarded=False) -> Tool:
    return Tool(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}, "required": []},
        func=func,
        guarded=guarded,
    )


def test_run_task_runs_prompt_and_records_ok():
    add_task("say hi", 60)
    task = list_tasks()[0]
    brain = _ScriptedBrain([[TextDelta("done"), _response("done")]])

    result = run_task(task, brain, [])

    assert result == "done"
    updated = list_tasks()[0]
    assert updated.last_status == "ok"
    assert updated.last_result == "done"
    assert updated.last_run_at is not None


def test_run_task_runs_readonly_tools_unattended():
    # A read-only tool runs with no approver present (the polling case).
    add_task("poll and remember", 60)
    task = list_tasks()[0]
    ran: list[dict] = []
    fetch = _runner_tool("fetch_url", lambda a: ran.append(a) or "page body")
    call = ToolCall(id="c1", name="fetch_url", arguments={})
    brain = _ScriptedBrain(
        [[_response(tool_calls=[call])], [TextDelta("fetched"), _response("fetched")]]
    )

    result = run_task(task, brain, [fetch])

    assert ran == [{}]  # the read-only tool actually ran
    assert result == "fetched"
    assert list_tasks()[0].last_status == "ok"


def test_run_task_blocks_guarded_tools_fail_closed():
    # No human is present, so a guarded tool must NOT run (approver=None).
    add_task("write a file", 60)
    task = list_tasks()[0]
    ran: list[dict] = []
    guarded = _runner_tool("write_file", lambda a: ran.append(a) or "wrote", guarded=True)
    call = ToolCall(id="c1", name="write_file", arguments={})
    brain = _ScriptedBrain(
        [[_response(tool_calls=[call])], [TextDelta("could not write"), _response("could not write")]]
    )

    result = run_task(task, brain, [guarded])

    assert ran == []  # guarded tool never ran unattended
    assert result == "could not write"
    assert list_tasks()[0].last_status == "ok"  # the turn itself completed fine


def test_run_task_records_error_when_loop_raises(monkeypatch):
    add_task("boom", 60)
    task = list_tasks()[0]

    def _boom(*args, **kwargs):
        raise RuntimeError("brain exploded")

    monkeypatch.setattr("vegapunk.loop.run", _boom)

    result = run_task(task, None, [])

    assert "brain exploded" in result  # error surfaced in the result, not raised
    updated = list_tasks()[0]
    assert updated.last_status == "error"
    assert "brain exploded" in updated.last_result


def test_run_task_does_not_swallow_keyboard_interrupt(monkeypatch):
    # KeyboardInterrupt is not an Exception; it must propagate so Ctrl-C stops.
    add_task("x", 60)
    task = list_tasks()[0]

    def _interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("vegapunk.loop.run", _interrupt)

    with pytest.raises(KeyboardInterrupt):
        run_task(task, None, [])


def test_record_run_advances_schedule_from_recorded_stamp():
    _insert_task("a" * 32, "task", "2026-01-01T00:00:00.000000Z", interval_seconds=300)
    task = list_tasks()[0]

    record_run(task, "ok", "did the thing", now="2026-06-01T12:00:00.000000Z")

    updated = list_tasks()[0]
    assert updated.last_run_at == "2026-06-01T12:00:00.000000Z"
    assert updated.last_status == "ok"
    assert updated.last_result == "did the thing"
    # next_run_at derives from the SAME recorded stamp + interval (300s), not a
    # second wall-clock read: recorded 12:00:00 + 5 min == 12:05:00 exactly.
    assert updated.next_run_at == "2026-06-01T12:05:00.000000Z"


def test_record_run_trims_long_result():
    _insert_task("a" * 32, "task", "2026-01-01T00:00:00.000000Z")
    task = list_tasks()[0]

    record_run(task, "ok", "x" * 5000, now="2026-06-01T00:00:00.000000Z")

    stored = list_tasks()[0].last_result
    assert stored.endswith("…[truncated]")
    assert stored.startswith("x" * 2000)
    assert len(stored) < 5000  # actually trimmed, not stored whole


def test_record_run_degrades_when_db_unavailable(monkeypatch, capsys):
    _insert_task("a" * 32, "task", "2026-01-01T00:00:00.000000Z")
    task = list_tasks()[0]

    def _boom(*args, **kwargs):
        raise db.StoreError("db is toast")

    monkeypatch.setattr("vegapunk.db.execute", _boom)
    record_run(task, "ok", "result", now="2026-06-01T00:00:00.000000Z")  # must not raise
    assert "could not record run" in capsys.readouterr().err


# --- the ticker (Scheduler) ---


class _RecordingBrain(Brain):
    """A reusable brain that answers every think() with a plain reply and counts
    the calls — lets a test see how many task turns actually ran."""

    def __init__(self) -> None:
        self.calls = 0

    def think(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[ThinkEvent]:
        self.calls += 1
        yield TextDelta("done")
        yield _response("done")


def test_run_due_now_runs_due_tasks_and_records():
    _insert_task("a" * 32, "due", "2000-01-01T00:00:00.000000Z", interval_seconds=60)
    brain = _RecordingBrain()
    scheduler = Scheduler(lambda: brain, [], threading.Lock())

    scheduler.run_due_now()

    assert brain.calls == 1  # the due task ran once
    assert list_tasks()[0].last_status == "ok"


def test_run_due_now_skips_tasks_not_yet_due():
    _insert_task("a" * 32, "future", "2999-01-01T00:00:00.000000Z")
    brain = _RecordingBrain()
    scheduler = Scheduler(lambda: brain, [], threading.Lock())

    scheduler.run_due_now()

    assert brain.calls == 0  # nothing due, nothing ran
    assert list_tasks()[0].last_status is None


def test_run_due_now_serializes_on_the_shared_lock():
    # The shared lock is what keeps a background task turn and a foreground user
    # turn off the single model/DB connection at once. While the lock is held,
    # run_due_now must not run any task; it proceeds only once the lock frees.
    _insert_task("a" * 32, "due", "2000-01-01T00:00:00.000000Z", interval_seconds=60)
    brain = _RecordingBrain()
    lock = threading.Lock()
    scheduler = Scheduler(lambda: brain, [], lock)

    lock.acquire()  # stand in for a foreground turn holding the lock
    worker = threading.Thread(target=scheduler.run_due_now)
    worker.start()
    try:
        time.sleep(0.05)  # give the worker time to block on the lock
        assert brain.calls == 0  # cannot have run — we hold the lock
    finally:
        lock.release()
        worker.join(timeout=2)
    assert brain.calls == 1  # ran once the lock was free


def test_run_due_now_bails_out_when_stop_is_signaled():
    # A stop requested mid-shutdown must not start a fresh task turn.
    _insert_task("a" * 32, "due", "2000-01-01T00:00:00.000000Z")
    brain = _RecordingBrain()
    scheduler = Scheduler(lambda: brain, [], threading.Lock())
    scheduler._stop.set()  # simulate stop() already requested

    scheduler.run_due_now()

    assert brain.calls == 0  # short-circuited before running the due task


def test_start_is_idempotent():
    # A long poll parks the ticker immediately, so no task runs during the check.
    scheduler = Scheduler(lambda: _RecordingBrain(), [], threading.Lock(), poll_seconds=60)
    scheduler.start()
    try:
        first = scheduler._thread
        scheduler.start()  # second call is a no-op
        assert scheduler._thread is first  # same thread, not a second one
    finally:
        scheduler.stop()


def test_stop_is_noop_when_never_started():
    scheduler = Scheduler(lambda: _RecordingBrain(), [], threading.Lock())
    scheduler.stop()  # must not raise


def test_ticker_runs_due_tasks_in_the_background():
    _insert_task("a" * 32, "due", "2000-01-01T00:00:00.000000Z", interval_seconds=60)
    ran = threading.Event()

    class _EventBrain(Brain):
        def think(self, messages, tools=None):
            ran.set()
            yield TextDelta("done")
            yield _response("done")

    scheduler = Scheduler(lambda: _EventBrain(), [], threading.Lock(), poll_seconds=0.02)
    scheduler.start()
    try:
        assert ran.wait(timeout=2)  # the ticker picked the task up on its own
    finally:
        scheduler.stop()
    assert list_tasks()[0].last_status == "ok"  # and recorded the run


def test_ticker_survives_a_failing_tick(monkeypatch, capsys):
    # A run_due_now that raises must be logged and the ticker must keep going —
    # a background thread that dies silently would strand every later task.
    calls = {"n": 0}
    second_tick = threading.Event()

    def _sometimes_boom(self):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("bad tick")
        second_tick.set()

    monkeypatch.setattr(Scheduler, "run_due_now", _sometimes_boom)
    scheduler = Scheduler(lambda: _RecordingBrain(), [], threading.Lock(), poll_seconds=0.02)
    scheduler.start()
    try:
        assert second_tick.wait(timeout=2)  # a 2nd tick ran after the 1st raised
    finally:
        scheduler.stop()
    assert "tick failed" in capsys.readouterr().err


# --- the schedule_task tool ---


def test_schedule_task_tool_registered_and_unguarded():
    from vegapunk.tools import ALL_TOOLS

    tool = next(t for t in ALL_TOOLS if t.name == "schedule_task")
    assert tool.guarded is False  # writes its own table, not the workspace
    schema = tool.to_schema()["function"]["parameters"]
    assert schema["properties"]["prompt"] == {"type": "string"}
    assert schema["properties"]["interval_seconds"] == {"type": "integer"}
    assert set(schema["required"]) == {"prompt", "interval_seconds"}


def test_schedule_task_tool_creates_a_task():
    from vegapunk.tools.scheduler import schedule_task

    result = schedule_task("poll example.com and remember changes", 300)

    assert "Scheduled task" in result
    tasks = list_tasks()
    assert len(tasks) == 1
    assert tasks[0].prompt == "poll example.com and remember changes"
    assert tasks[0].interval_seconds == 300


def test_schedule_task_tool_surfaces_validation():
    # Validation lives in add_task; the tool passes the message straight through.
    from vegapunk.tools.scheduler import schedule_task

    assert "positive" in schedule_task("do a thing", 0)
    assert "Nothing to schedule" in schedule_task("   ", 60)
    assert list_tasks() == []  # neither rejection wrote a row

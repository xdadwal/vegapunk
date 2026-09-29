"""Durable scheduled-run evidence, isolated from user memory and other tasks."""

import pytest
from logpose import tool

from vegapunk import db, scheduler
from tests.fake_provider import agent_for, call, says, session_for, wants


@tool
def fetch_page() -> str:
    """Read a test page."""
    return "page body"


@tool
def write_it() -> str:
    """Represent an operation requiring approval."""
    raise AssertionError("guarded tool must not execute")


@pytest.fixture(autouse=True)
def _guard_test_write(monkeypatch):
    from vegapunk import gate
    monkeypatch.setattr(gate, "GUARDED", gate.GUARDED | {"write_it"})


def _task(prompt="explore"):
    scheduler.add_task(prompt, 60)
    return scheduler.list_tasks()[-1]


def test_runs_survive_reopen_and_task_removal():
    from vegapunk import task_history

    task = _task()
    for answer in ("first observation", "second observation"):
        agent, _ = agent_for(says(answer))
        scheduler.run_task(task, agent)
    db.close_connection()
    scheduler.remove_task(task.id)
    runs = task_history.list_runs(task.id)
    assert [r.result for r in runs] == ["second observation", "first observation"]
    assert all(r.status == "completed" and r.finished_at for r in runs)


def test_blocked_and_partial_are_based_on_tool_evidence():
    from vegapunk import task_history

    task = _task()
    agent, _ = agent_for([wants(call("write_it")), says("done")], tools=[write_it])
    scheduler.run_task(task, agent)
    assert task_history.list_runs(task.id)[0].status == "blocked"
    assert scheduler.list_tasks()[0].last_status == "blocked"
    agent, _ = agent_for(
        [wants(call("write_it"), call("fetch_page")), says("done")],
        tools=[write_it, fetch_page],
    )
    scheduler.run_task(task, agent)
    run = task_history.list_runs(task.id)[0]
    assert run.status == "partial"
    assert {e.outcome for e in task_history.events(run.id)} == {"blocked", "returned"}


def test_next_run_gets_only_its_own_bounded_history(monkeypatch):
    from vegapunk import task_history

    one = _task("one")
    two = _task("two")
    for task, result in ((one, "own observation " + "x" * 3000), (two, "private other task")):
        agent, _ = agent_for(says(result))
        scheduler.run_task(task, agent)
    seen = []
    monkeypatch.setattr("vegapunk.loop.run", lambda agent, prompt, **kwargs: seen.append(prompt) or "done")
    scheduler.run_task(one, None)
    assert "own observation" in seen[0]
    assert "private other task" not in seen[0]
    assert "untrusted" in seen[0].lower()
    assert len(seen[0]) < 2200
    assert len(task_history.list_runs(one.id)[1].result) < 2100


def test_history_failure_prevents_agent_execution(monkeypatch):
    from vegapunk import task_history

    task = _task()
    def fail(*args, **kwargs):
        raise db.StoreError("unavailable")
    monkeypatch.setattr(task_history, "begin", fail)
    monkeypatch.setattr("vegapunk.loop.run", lambda *a, **k: pytest.fail("must not run"))
    assert "Could not start" in scheduler.run_task(task, None)


def test_restart_recovers_open_run_and_defers_next_attempt():
    from vegapunk import task_history

    task = _task()
    run_id = task_history.begin(task)
    db.close_connection()
    stamp = "2099-01-01T00:00:00.000000Z"
    assert task_history.recover_interrupted(now=stamp) == 1
    run = task_history.list_runs(task.id)[0]
    assert run.id == run_id and run.status == "interrupted"
    assert run.finished_at == stamp
    assert scheduler.list_tasks()[0].next_run_at == db.stamp_plus(stamp, 60)
    assert task_history.recover_interrupted(now=stamp) == 0


def test_open_run_prevents_duplicate_execution():
    from vegapunk import task_history

    task = _task()
    task_history.begin(task)
    with pytest.raises(db.StoreError):
        task_history.begin(task)


def test_interrupt_is_recorded_and_still_propagates(monkeypatch):
    from vegapunk import task_history

    task = _task()
    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr("vegapunk.loop.run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        scheduler.run_task(task, None)
    assert task_history.list_runs(task.id)[0].status == "interrupted"


def test_history_command_lists_evidence_and_removed_tasks():
    from vegapunk.commands import CommandContext, dispatch

    ctx = CommandContext(session=session_for())

    task = _task()
    agent, _ = agent_for([wants(call("fetch_page")), says("observed")], tools=[fetch_page])
    scheduler.run_task(task, agent)
    scheduler.remove_task(task.id)
    output = dispatch(f"/schedule history {task.id[:8]}", ctx).output
    assert "observed" in output and "fetch_page:returned" in output
    assert "Usage:" in dispatch("/schedule history not-hex", ctx).output


def test_moltbook_auth_failure_is_blocked_and_raw_results_are_not_events(tmp_path, monkeypatch):
    from dataclasses import replace
    from vegapunk import task_history
    from vegapunk.tools import moltbook

    monkeypatch.setattr(moltbook, "config", replace(
        moltbook.config, moltbook_credentials_file=tmp_path / "missing.json",
    ))
    task = _task()
    agent, _ = agent_for([wants(call("moltbook_home")), says("check blocked")],
                         tools=[moltbook.moltbook_home])
    scheduler.run_task(task, agent)
    run = task_history.list_runs(task.id)[0]
    assert run.status == "blocked"
    assert task_history.events(run.id)[0].outcome == "blocked"
    assert "missing.json" not in repr(task_history.events(run.id))


def test_step_limit_and_token_limit_are_errors():
    from vegapunk import task_history

    task = _task()
    agent, _ = agent_for(wants(call("fetch_page")), tools=[fetch_page],
                         max_iterations=2, repeat_last=True)
    scheduler.run_task(task, agent)
    assert task_history.list_runs(task.id)[0].status == "error"
    agent, _ = agent_for(says("cut off", stop_reason="max_tokens"))
    scheduler.run_task(task, agent)
    assert task_history.list_runs(task.id)[0].status == "error"


def test_generic_error_strings_are_not_claimed_as_success(monkeypatch):
    import requests
    from vegapunk import task_history
    from vegapunk.tools import fetch

    def fail(*args, **kwargs):
        raise requests.ConnectionError("unreachable")
    monkeypatch.setattr(fetch, "_get", fail)
    task = _task()
    agent, _ = agent_for(
        [wants(call("fetch_url", {"url": "https://example.com"})), says("done")],
        tools=[fetch.fetch_url],
    )
    scheduler.run_task(task, agent)
    run = task_history.list_runs(task.id)[0]
    assert run.status == "completed"
    assert task_history.events(run.id)[0].outcome == "returned"


def test_failed_finish_leaves_open_run_and_stops_next_attempt(monkeypatch):
    from vegapunk import task_history

    task = _task()
    def fail(*args, **kwargs):
        raise db.StoreError("unavailable")
    monkeypatch.setattr(task_history, "finish", fail)
    agent, _ = agent_for(says("done"))
    assert "Could not persist" in scheduler.run_task(task, agent)
    monkeypatch.setattr("vegapunk.loop.run", lambda *a, **k: pytest.fail("must not retry"))
    assert "Could not start" in scheduler.run_task(task, None)
    assert task_history.list_runs(task.id)[0].status == "running"


def test_event_storage_failure_aborts_subsequent_provider_turn(monkeypatch):
    from vegapunk import task_history

    task = _task()
    original = db.execute
    def fail_event(sql, *args):
        if "scheduled_run_events" in sql:
            raise db.StoreError("event store unavailable")
        return original(sql, *args)
    monkeypatch.setattr(db, "execute", fail_event)
    agent, provider = agent_for(
        [wants(call("fetch_page")), says("must not reach this turn")], tools=[fetch_page],
    )
    result = scheduler.run_task(task, agent)
    assert "event store unavailable" in result
    assert task_history.list_runs(task.id)[0].status == "error"
    assert len(provider.requests) == 1


def test_v5_upgrade_preserves_tasks_and_backup_contains_history(tmp_path):
    import sqlite3
    from vegapunk import task_history

    task = _task("existing v5 task")
    db.execute("DROP TABLE scheduled_run_events")
    db.execute("DROP TABLE scheduled_runs")
    db.execute("UPDATE meta SET value = '5' WHERE key = 'schema_version'")
    db.close_connection()
    assert scheduler.list_tasks()[0].prompt == "existing v5 task"
    assert db.query("SELECT value FROM meta WHERE key = 'schema_version'") == [("6",)]
    agent, _ = agent_for(says("preserved observation"))
    scheduler.run_task(task, agent)
    snapshot = db.backup_now()
    with sqlite3.connect(snapshot) as conn:
        assert conn.execute("SELECT result FROM scheduled_runs").fetchall() == [("preserved observation",)]


def test_finish_rolls_back_history_if_schedule_update_fails(monkeypatch):
    from contextlib import contextmanager
    from vegapunk import task_history

    task = _task()
    run_id = task_history.begin(task)
    original = db.transaction

    class FailingConnection:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, sql, params=()):
            if sql.startswith("UPDATE scheduled_tasks"):
                raise db.StoreError("schedule write failed")
            return self.conn.execute(sql, params)

    @contextmanager
    def transaction(**kwargs):
        with original(**kwargs) as conn:
            yield FailingConnection(conn)

    monkeypatch.setattr(db, "transaction", transaction)
    with pytest.raises(db.StoreError, match="schedule write failed"):
        task_history.finish(task, run_id, "completed", "done")
    assert task_history.list_runs(task.id)[0].status == "running"
    assert scheduler.list_tasks()[0].last_status is None


def test_abrupt_worker_exit_leaves_recoverable_start_record():
    import os
    import subprocess
    import sys
    from vegapunk import task_history

    task = _task()
    path = db.db_path()
    db.close_connection()
    child = subprocess.run(
        [sys.executable, "-c",
         "import os; from vegapunk import scheduler, loop; "
         "loop.run = lambda *a, **k: os._exit(17); "
         "scheduler.run_task(scheduler.list_tasks()[0], None)"],
        env={**os.environ, "VEGAPUNK_DB_FILE": str(path)},
        capture_output=True, text=True, timeout=30,
    )
    assert child.returncode == 17, child.stderr
    assert task_history.list_runs(task.id)[0].status == "running"
    assert task_history.recover_interrupted() == 1
    assert task_history.list_runs(task.id)[0].status == "interrupted"


def test_concurrent_starts_claim_only_one_run():
    from concurrent.futures import ThreadPoolExecutor
    from vegapunk import task_history

    task = _task()
    def attempt():
        try:
            return task_history.begin(task)
        except db.StoreError:
            return None
        finally:
            db.close_connection()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: attempt(), range(2)))
    assert sum(result is not None for result in results) == 1
    assert len(task_history.list_runs(task.id)) == 1


def test_context_keeps_only_three_latest_summaries():
    from vegapunk import task_history

    task = _task()
    for index in range(5):
        run_id = task_history.begin(task)
        task_history.finish(task, run_id, "completed", f"observation-{index}")
    context = task_history.context(task.id)
    assert "observation-0" not in context and "observation-1" not in context
    assert all(f"observation-{i}" in context for i in (2, 3, 4))


def test_moltbook_success_records_read_without_raw_body(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from vegapunk import task_history
    from vegapunk.tools import moltbook

    credentials = tmp_path / "credentials.json"
    credentials.write_text('{"api_key":"test-secret"}')
    monkeypatch.setattr(moltbook, "config", replace(moltbook.config, moltbook_credentials_file=credentials))
    monkeypatch.setattr(moltbook, "_get", lambda *a, **k: SimpleNamespace(
        status_code=200, headers={}, raise_for_status=lambda: None,
        json=lambda: {"body": "raw account content"},
    ))
    task = _task()
    agent, _ = agent_for([wants(call("moltbook_home")), says("observed account")],
                         tools=[moltbook.moltbook_home])
    scheduler.run_task(task, agent)
    run = task_history.list_runs(task.id)[0]
    assert run.status == "success"
    assert task_history.events(run.id)[0].outcome == "success"
    assert "raw account content" not in repr(task_history.events(run.id))
    assert "test-secret" not in repr(run)

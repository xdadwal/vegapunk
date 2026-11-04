"""Lifecycle snapshots survive transcript rollback and retain evidence links."""

import asyncio
import pytest
from logpose import ToolObservation, ToolUseBlock, ToolResultBlock
from logpose.runtime import RuntimeContext
from vegapunk import artifacts, db, execution, progress, session_store


def test_lifecycle_checkpoints_include_plan_and_tool_evidence_after_interruption():
    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    plan = progress.create(sid, "Inspect", ["Check"])
    run = execution.start_run(sid, "inspect")
    callback = execution.tool_observer(lambda: sid)
    ctx = RuntimeContext(run_id="runtime", provider="fake", model="fake")
    call = ToolUseBlock(id="call", name="inspect", input={})
    asyncio.run(callback(ToolObservation("intent", ctx, call)))
    asyncio.run(callback(ToolObservation("outcome", ctx, call, ToolResultBlock(tool_use_id="call", content="failed", is_error=True))))
    execution.finish_run(run, "interrupted")
    rows = progress.checkpoints(sid)
    reasons = {r["reason"] for r in rows}
    assert {"turn_started", "tool_intent", "tool_outcome", "turn_interrupted"} <= reasons
    observed = next(r for r in rows if r["reason"] == "tool_outcome")
    assert observed["plan"]["id"] == plan["id"]
    assert observed["execution"]["tool_status"] == "error"
    linked = artifacts.checkpoint_artifacts(sid, observed["id"])
    assert artifacts.read(sid, linked[0]["id"])["text"] == "failed"
    assert session_store.load_session("one") == []


def test_planless_recovery_checkpoint_is_scoped_and_deleted_with_session():
    session_store.save_session("one", [])
    session_store.save_session("two", [])
    sid = session_store.session_identity("one")
    execution.start_run(sid, "unfinished")
    execution.recover_runs()
    rows = progress.checkpoints(sid)
    recovered = next(r for r in rows if r["reason"] == "recovery_abandoned")
    assert recovered["plan"] is None
    assert recovered["execution"]["run_status"] == "abandoned"
    saved = artifacts.store_text(sid, "evidence", "observed")
    artifacts.link_checkpoint(sid, saved["id"], recovered["id"])
    with pytest.raises(ValueError):
        artifacts.link_checkpoint(session_store.session_identity("two"), saved["id"], recovered["id"])
    session_store.delete_session("one")
    assert not db.query("SELECT 1 FROM lifecycle_checkpoints WHERE session_id=?", (sid,))


def test_recovery_keeps_original_request_separate_from_staged_model_input():
    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    execution.start_run(sid, "inspect service", staged_input="[skill instructions]\ninspect service")
    execution.recover_runs()
    run = execution.list_runs(sid)[0]
    assert run.request == "inspect service"
    assert run.staged_input == "[skill instructions]\ninspect service"
    assert run.status == "abandoned"


def test_schema21_migration_preserves_old_run_request():
    import sqlite3
    db.close_connection()
    with sqlite3.connect(db.db_path()) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta VALUES ('schema_version','21')")
        conn.execute("CREATE TABLE execution_runs (run_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,request TEXT NOT NULL,status TEXT NOT NULL,started_at TEXT NOT NULL,finished_at TEXT)")
        conn.execute("INSERT INTO execution_runs VALUES ('run','session','old request','completed','now','now')")
    assert db.query("SELECT request,staged_input FROM execution_runs") == [("old request", "")]
    assert db.query("SELECT value FROM meta WHERE key='schema_version'") == [(str(db.SCHEMA_VERSION),)]


def test_checkpoint_commands_paginate_resolve_and_complete_only_current_session():
    from prompt_toolkit.document import Document
    from prompt_toolkit.completion import CompleteEvent
    from vegapunk.commands import CommandContext, dispatch
    from vegapunk.prompter import SlashCompleter
    session_store.save_session("one", [])
    session_store.save_session("two", [])
    sid = session_store.session_identity("one")
    other = session_store.session_identity("two")
    for _ in range(26):
        execution.finish_run(execution.start_run(sid, "test"), "completed")
    execution.start_run(other, "other")
    assert len(progress.checkpoints(sid)) == 50
    assert len(progress.checkpoints(sid, 50)) == 2
    latest = progress.checkpoints(sid)[0]
    ctx = CommandContext(session=None, current_name="one")
    assert latest["id"] in dispatch(f"/checkpoints show {latest['id']}", ctx).output
    assert "turn_started" in dispatch("/checkpoints list 50", ctx).output
    assert "Could not read" in dispatch("/checkpoints list -1", ctx).output
    assert "Unknown checkpoint" in dispatch(f"/checkpoints show {progress.checkpoints(other)[0]['id']}", ctx).output
    completer = SlashCompleter(session_identity=lambda: sid)
    choices = {c.text for c in completer.get_completions(Document("/checkpoints show "), CompleteEvent())}
    assert latest["id"] in choices and progress.checkpoints(other)[0]["id"] not in choices


def test_checkpoint_failure_rolls_back_tool_intent_before_effect(monkeypatch):
    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    def fail(*args, **kwargs):
        raise db.StoreError("checkpoint write failed")
    monkeypatch.setattr(execution, "_checkpoint", fail)
    event = ToolObservation("intent", RuntimeContext(run_id="runtime", provider="fake", model="fake"), ToolUseBlock(id="call", name="write", input={}))
    with pytest.raises(db.StoreError, match="checkpoint"):
        execution._persist_tool(sid, event)
    assert execution.tool_observations(sid) == []


def test_plan_status_row_and_resume_report_recovery_without_replay():
    from types import SimpleNamespace
    from vegapunk.cli import _status_line
    from vegapunk.commands import CommandContext, dispatch
    from tests.fake_provider import session_for, says
    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    plan = progress.create(sid, "Inspect", ["Check service"])
    progress.update(sid, plan["id"], 0, "in_progress", 1)
    event = ToolObservation("intent", RuntimeContext(run_id="runtime", provider="fake", model="fake"), ToolUseBlock(id="call", name="write", input={}))
    execution._persist_tool(sid, event)
    stub = SimpleNamespace(session_id=sid, model_label="fake", context_tokens=None, context_window=0)
    assert "Plan 0/1 · Check service" in _status_line(CommandContext(session=stub))
    session = session_for([says("unused")])
    try:
        output = dispatch("/sessions one", CommandContext(session=session)).output
        assert "Active plan:" in output and "Latest checkpoint:" in output
        assert "1 uncertain tool outcome" in output
        assert session.messages == []
        assert session.backend.provider.call_count == 0
    finally:
        session.close()

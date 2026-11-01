"""Tool journals preserve evidence independently of conversation rollback."""

import asyncio
import pytest
from logpose import ToolObservation, ToolUseBlock, ToolResultBlock
from logpose.runtime import RuntimeContext

from vegapunk import artifacts, execution, session_store


def test_runs_reports_journal_database_failure(monkeypatch):
    from vegapunk import db
    from vegapunk.commands import CommandContext, dispatch

    session_store.save_session("one", [])

    def unavailable(*args):
        raise db.StoreError("unavailable")

    monkeypatch.setattr(execution, "tool_observations", unavailable)
    result = dispatch("/runs", CommandContext(session=None, current_name="one"))
    assert "Could not read runs" in result.output


def test_observer_records_intent_and_retrievable_outcome():
    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    callback = execution.tool_observer(lambda: sid)
    call = ToolUseBlock(id="call", name="inspect", input={})
    context = RuntimeContext(run_id="runtime", provider="fake", model="fake")
    asyncio.run(callback(ToolObservation("intent", context, call)))
    asyncio.run(callback(ToolObservation("outcome", context, call, ToolResultBlock(tool_use_id="call", content="evidence", is_error=False))))
    rows = execution.tool_observations(sid)
    assert rows[0]["status"] == "completed"
    assert artifacts.read(sid, rows[0]["artifact_id"])["text"] == "evidence"


def test_unmatched_intent_is_reported_as_unknown():
    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    callback = execution.tool_observer(lambda: sid)
    asyncio.run(callback(ToolObservation("intent", RuntimeContext(run_id="runtime", provider="fake", model="fake"), ToolUseBlock(id="call", name="write", input={}))))
    assert execution.tool_observations(sid)[0]["status"] == "outcome_unknown"


def test_session_records_returned_results_through_real_agent_loop():
    from logpose import tool
    from tests.fake_provider import session_for, call, wants, says

    @tool
    def inspect() -> str:
        """Return an observed result."""
        return "observed evidence"

    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    session = session_for([wants(call("inspect")), says("done")], tools=[inspect])
    session.session_id = sid
    try:
        list(session.send("inspect"))
        row = execution.tool_observations(sid)[0]
        assert artifacts.read(sid, row["artifact_id"])["text"] == "observed evidence"
    finally:
        session.close()


def test_outcome_storage_failure_stops_without_repeating_effect(monkeypatch):
    from logpose import tool, ToolObservationError
    from tests.fake_provider import session_for, call, wants, says

    effects = []

    @tool
    def effect() -> str:
        """Perform one observed effect."""
        effects.append(1)
        return "done"

    def unavailable(*args, **kwargs):
        raise OSError("storage unavailable")

    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    session = session_for([wants(call("effect")), says("must not run")], tools=[effect])
    session.session_id = sid
    monkeypatch.setattr(artifacts, "store_text", unavailable)
    try:
        with pytest.raises(ToolObservationError):
            list(session.send("act"))
        assert effects == [1]
        assert session.messages == []
        assert execution.tool_observations(sid)[0]["status"] == "outcome_unknown"
    finally:
        session.close()


def test_real_shell_result_can_be_retrieved_without_rerunning(tmp_path, monkeypatch):
    from dataclasses import replace
    from tests.fake_provider import session_for, call, wants, says
    from vegapunk.approval import CLIApprover, ApprovalPolicy
    from vegapunk.tools import workspace
    from vegapunk.tools.shell import run_shell

    monkeypatch.setattr(workspace, "config", replace(workspace.config, workspace_root=tmp_path))
    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    session = session_for([wants(call("run_shell", {"command": "printf evidence"})), says("done")],
                          tools=[run_shell], approver=CLIApprover(ApprovalPolicy(auto=True)))
    session.session_id = sid
    try:
        list(session.send("inspect"))
        row = execution.tool_observations(sid)[0]
        assert artifacts.read(sid, row["artifact_id"])["text"] == "[exit 0]\nevidence"
    finally:
        session.close()

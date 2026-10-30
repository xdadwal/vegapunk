"""Durable execution records retain requests and observed state."""

from vegapunk import db, execution, session_store


def test_interrupted_request_survives_session_rename():
    session_store.save_session("untitled", [])
    identity = session_store.session_identity("untitled")
    run = execution.start_run(identity, "inspect the server")
    execution.finish_run(run, "interrupted")
    session_store.rename_session("untitled", "server", [])
    records = execution.list_runs(session_store.session_identity("server"))
    assert records[0].request == "inspect the server"
    assert records[0].status == "interrupted"


def test_recovery_marks_only_unfinished_runs():
    session_store.save_session("session", [])
    identity = session_store.session_identity("session")
    execution.start_run(identity, "unfinished")
    completed = execution.start_run(identity, "finished")
    execution.finish_run(completed, "completed")
    execution.recover_runs()
    statuses = {run.request: run.status for run in execution.list_runs(identity)}
    assert statuses == {"unfinished": "abandoned", "finished": "completed"}


def test_completion_persists_transcript_with_ordered_events():
    from tests.fake_provider import user_turn, assistant_turn

    session_store.save_session("session", [])
    run = execution.start_run(session_store.session_identity("session"), "hello")
    messages = [user_turn("hello"), assistant_turn("hi")]
    execution.finish_run(run, "completed", messages)
    assert session_store.load_session("session") == messages
    assert db.query("SELECT sequence,status FROM execution_events WHERE run_id=? ORDER BY sequence", (run,)) == [(1, "running"), (2, "completed")]

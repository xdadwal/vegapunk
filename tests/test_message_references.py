"""Stable source identity follows the unchanged prefix, never edited positions."""

import pytest
from tests.fake_provider import user_turn, assistant_turn
from vegapunk import db, execution, message_refs, session_store


def test_append_rename_and_edit_preserve_only_unchanged_prefix_ids():
    messages = [user_turn("request"), assistant_turn("answer")]
    session_store.save_session("one", messages)
    sid = session_store.session_identity("one")
    first = message_refs.references(sid)
    session_store.save_session("one", messages + [user_turn("continue")])
    appended = message_refs.references(sid)
    assert appended[:2] == first
    session_store.rename_session("one", "renamed", messages + [user_turn("continue")])
    assert message_refs.references(sid) == appended
    changed = [messages[0], assistant_turn("corrected"), user_turn("continue")]
    session_store.save_session("renamed", changed)
    edited = message_refs.references(sid)
    assert edited[0] == first[0]
    assert edited[1]["message_id"] != first[1]["message_id"]
    assert edited[2]["message_id"] != appended[2]["message_id"]
    with pytest.raises(ValueError, match="Unknown"):
        message_refs.read(sid, first[1]["message_id"], first[1]["content_hash"])


def test_references_are_hash_checked_and_session_scoped():
    message = user_turn("private")
    session_store.save_session("one", [message])
    session_store.save_session("two", [message])
    sid = session_store.session_identity("one")
    ref = message_refs.references(sid)[0]
    assert message_refs.read(sid, ref["message_id"], ref["content_hash"]) == message
    with pytest.raises(ValueError):
        message_refs.read(session_store.session_identity("two"), ref["message_id"], ref["content_hash"])
    with pytest.raises(ValueError, match="hash"):
        message_refs.read(sid, ref["message_id"], "bad")
    db.execute("UPDATE sessions SET messages='[]' WHERE session_id=?", (sid,))
    with pytest.raises(ValueError, match="changed"):
        message_refs.read(sid, ref["message_id"], ref["content_hash"])


def test_finish_run_and_delete_update_reference_rows_atomically(monkeypatch):
    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    run = execution.start_run(sid, "request")
    execution.finish_run(run, "completed", [user_turn("request"), assistant_turn("done")])
    assert len(message_refs.references(sid)) == 2
    def fail(*args):
        raise db.StoreError("reference write failed")
    monkeypatch.setattr(message_refs, "sync", fail)
    with pytest.raises(db.StoreError):
        session_store.save_session("one", [])
    assert len(session_store.load_session("one")) == 2
    session_store.delete_session("one")
    assert not db.query("SELECT 1 FROM session_message_ids WHERE session_id=?", (sid,))


def test_schema22_backfill_preserves_transcripts_and_reports_malformed_rows(capsys):
    import json
    import sqlite3
    encoded = json.dumps([user_turn("existing source")])
    db.close_connection()
    with sqlite3.connect(db.db_path()) as conn:
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta VALUES ('schema_version','22')")
        conn.execute("CREATE TABLE sessions (slug TEXT PRIMARY KEY,messages TEXT NOT NULL,turns INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,conversation_mode TEXT NOT NULL DEFAULT 'conversation',agent_id TEXT NOT NULL DEFAULT 'default',model_selector TEXT NOT NULL DEFAULT '',effort TEXT NOT NULL DEFAULT '',session_id TEXT)")
        conn.execute("INSERT INTO sessions(slug,messages,turns,created_at,updated_at,session_id) VALUES ('one',?,1,'now','now','session')", (encoded,))
        conn.execute("INSERT INTO sessions(slug,messages,turns,created_at,updated_at,session_id) VALUES ('bad','invalid JSON',1,'now','now','bad-session')")
    ref = message_refs.references("session")[0]
    assert db.query("SELECT messages FROM sessions WHERE slug='one'") == [(encoded,)]
    assert "1 malformed transcript" in capsys.readouterr().err
    assert message_refs.references("bad-session") == []
    db.close_connection()
    assert message_refs.references("session")[0] == ref


def test_summary_source_artifact_carries_hash_bound_message_identity():
    import asyncio
    import json
    from dataclasses import replace
    from logpose import ContextInput, Message, TextBlock
    from logpose.providers.base import ToolSpec
    from tests.fake_provider import FakeProvider, backend_for, says
    from vegapunk import context_summary, artifacts
    from vegapunk.config import config
    messages = (Message(role="user", content=[TextBlock(text="Keep private")]),
                Message(role="assistant", content=[TextBlock(text="Unverified observation")]),
                Message(role="user", content=[TextBlock(text="Continue")]))
    stored = [m.model_dump(mode="json") for m in messages]
    session_store.save_session("one", stored)
    sid = session_store.session_identity("one")
    ref = message_refs.references(sid)[1]
    request = ContextInput(messages, "system", (ToolSpec("read_artifact", "retrieve", {}),), "fake", 100)
    output = json.dumps({"notes": [{"text": "Unverified", "source": "1:0", "quote": "Unverified", "kind": "uncertainty"}]})
    backend = replace(backend_for(), spawn_provider=lambda: FakeProvider(says(output)))
    _, system, _ = asyncio.run(context_summary.summarize(request, messages, sid, replace(config, context_recent_turns=1), lambda: backend))
    source_id = json.loads(system.rsplit("\n", 1)[-1])["source_artifact"]
    sources = json.loads(artifacts.read(sid, source_id)["text"])
    source = next(s for s in sources if s["id"] == "1:0")
    assert source["message_id"] == ref["message_id"]
    assert source["content_hash"] == ref["content_hash"]
    session_store.save_session("one", [stored[0], assistant_turn("edited")])
    with pytest.raises(ValueError, match="Unknown"):
        message_refs.read(sid, source["message_id"], source["content_hash"])
    assert "Unverified observation" in artifacts.read(sid, source_id)["text"]


def test_finish_failure_preserves_prior_transcript_and_running_request(monkeypatch):
    session_store.save_session("one", [user_turn("prior")])
    sid = session_store.session_identity("one")
    original = message_refs.references(sid)
    run = execution.start_run(sid, "next")
    def fail(*args):
        raise db.StoreError("reference write failed")
    monkeypatch.setattr(message_refs, "sync", fail)
    with pytest.raises(db.StoreError):
        execution.finish_run(run, "completed", [user_turn("prior"), user_turn("next")])
    assert execution.list_runs(sid)[0].status == "running"
    assert session_store.load_session("one") == [user_turn("prior")]
    assert message_refs.references(sid) == original


def test_database_snapshot_keeps_stable_source_references(monkeypatch):
    session_store.save_session("one", [user_turn("source")])
    sid = session_store.session_identity("one")
    ref = message_refs.references(sid)[0]
    backup = db.backup_now()
    db.close_connection()
    monkeypatch.setattr(db, "db_path", lambda: backup)
    assert message_refs.references(sid)[0] == ref
    assert message_refs.read(sid, ref["message_id"], ref["content_hash"]) == user_turn("source")


def test_reference_backfill_can_hash_escaped_legacy_surrogates():
    session_store.save_session("one", [user_turn("legacy \ud800 value")])
    sid = session_store.session_identity("one")
    ref = message_refs.references(sid)[0]
    db.execute("UPDATE meta SET value='22' WHERE key='schema_version'")
    db.close_connection()
    assert message_refs.references(sid)[0] == ref
    assert message_refs.read(sid, ref["message_id"], ref["content_hash"]) == user_turn("legacy \ud800 value")

"""Sourced learning stays local, attributable, and isolated across tasks."""

import json
from dataclasses import replace

import pytest

from vegapunk import db, scheduler
from tests.fake_provider import agent_for, call, says, wants


class Response:
    status_code = 200
    headers = {}
    def __init__(self, data):
        self.data = data
    def json(self):
        return self.data
    def raise_for_status(self):
        return None


@pytest.fixture
def platform(tmp_path, monkeypatch):
    from vegapunk.tools import moltbook
    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"api_key": "notebook-test-key"}))
    monkeypatch.setattr(moltbook, "config", replace(moltbook.config, moltbook_credentials_file=path))
    payload = {"post": {"id": "post-1", "title": "Agent continuity", "content": "Durable receipts prevent duplicate replies.",
                        "author": {"name": "Peer"}}}
    monkeypatch.setattr(moltbook, "_get", lambda *a, **k: Response(payload))
    return moltbook, path, payload


def task(prompt="explore"):
    scheduler.add_task(prompt, 1800)
    return scheduler.list_tasks()[-1]


def observe(scheduled, moltbook):
    agent, provider = agent_for([wants(call("moltbook_post", {"post_id": "post-1"})), says("read post")],
                                tools=[moltbook.moltbook_post])
    scheduler.run_task(scheduled, agent)
    return provider


def note(scheduled, source, **extra):
    from vegapunk.tools import moltbook_notebook as tools
    arguments = {"kind": "hypothesis", "subject": "continuity", "text": "Receipts may prevent duplicate replies.",
                 "source_id": source, "quote": "Durable receipts prevent duplicate replies.", **extra}
    agent, provider = agent_for([wants(call("moltbook_note", arguments)), says("considered")], tools=[tools.moltbook_note])
    scheduler.run_task(scheduled, agent)
    return provider


def test_read_captures_versioned_sources_without_personal_memory(platform):
    moltbook, _, payload = platform
    scheduled = task()
    observe(scheduled, moltbook)
    rows = db.query("SELECT id,excerpt,seen_count FROM moltbook_sources")
    assert len(rows) == 1
    assert "Durable receipts" in rows[0][1]
    observe(scheduled, moltbook)
    assert db.query("SELECT seen_count FROM moltbook_sources") == [(2,)]
    payload["post"]["content"] = "The peer changed the claim."
    observe(scheduled, moltbook)
    assert db.query("SELECT COUNT(*) FROM moltbook_sources") == [(2,)]
    assert db.query("SELECT COUNT(*) FROM memory") == [(0,)]


def test_note_requires_exact_evidence_and_deduplicates(platform):
    moltbook, _, _ = platform
    scheduled = task()
    observe(scheduled, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    note(scheduled, source, quote="A claim nobody made.")
    assert db.query("SELECT COUNT(*) FROM moltbook_notes") == [(0,)]
    note(scheduled, source)
    note(scheduled, source)
    rows = db.query("SELECT kind,confidence,quote FROM moltbook_notes")
    assert rows == [("hypothesis", "medium", "Durable receipts prevent duplicate replies.")]


def test_task_and_credential_isolation(platform):
    moltbook, path, _ = platform
    one, two = task("one"), task("two")
    observe(one, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    note(two, source)
    assert db.query("SELECT COUNT(*) FROM moltbook_notes") == [(0,)]
    path.write_text(json.dumps({"api_key": "rotated-test-key"}))
    note(one, source)
    assert db.query("SELECT COUNT(*) FROM moltbook_notes") == [(0,)]


def test_supersession_preserves_original_evidence(platform):
    moltbook, _, _ = platform
    scheduled = task()
    observe(scheduled, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    note(scheduled, source)
    old = db.query("SELECT id FROM moltbook_notes")[0][0]
    note(scheduled, source, text="Receipts help, but cannot prove visibility.", supersedes=old)
    rows = db.query("SELECT status,supersedes FROM moltbook_notes ORDER BY created_at")
    assert rows == [("superseded", ""), ("active", old)]


def test_followup_returns_in_next_run_as_untrusted_context(platform, monkeypatch):
    moltbook, _, _ = platform
    scheduled = task()
    observe(scheduled, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    note(scheduled, source, kind="follow_up", text="Revisit the receipt discussion.", follow_up_on="2026-01-01")
    seen = []
    monkeypatch.setattr("vegapunk.loop.run", lambda agent, prompt, **kwargs: seen.append(prompt) or "done")
    scheduler.run_task(scheduled, None)
    assert "Revisit the receipt discussion." in seen[0]
    assert "untrusted model interpretations" in seen[0]


def test_notebook_tools_cannot_be_called_without_scheduled_context(platform):
    from vegapunk.tools.moltbook_notebook import moltbook_note, moltbook_notebook
    assert "Blocked:" in moltbook_notebook()
    assert "Blocked:" in moltbook_note("hypothesis", "topic", "claim", "source", "quote")


def tool_result(provider):
    from logpose import ToolResultBlock
    return next(block.content for message in provider.last_request.messages
                for block in message.content if isinstance(block, ToolResultBlock))


def invoke(scheduled, name, arguments=None):
    from vegapunk.tools import moltbook_notebook as tools
    agent, provider = agent_for([wants(call(name, arguments)), says("checked")], tools=[getattr(tools, name)])
    scheduler.run_task(scheduled, agent)
    return tool_result(provider)


def test_source_ids_reach_model_and_local_writes_are_not_remote_success(platform):
    moltbook, _, _ = platform
    scheduled = task()
    provider = observe(scheduled, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    assert source in tool_result(provider)
    provider = note(scheduled, source)
    assert "recorded:" in tool_result(provider)
    assert db.query("SELECT outcome FROM scheduled_run_events ORDER BY created_at") == [("success",), ("returned",)]
    assert db.query("SELECT COUNT(*) FROM moltbook_permissions") == [(0,)]
    assert db.query("SELECT COUNT(*) FROM moltbook_actions") == [(0,)]


def test_only_public_selected_fields_are_stored_and_key_is_redacted(platform):
    moltbook, _, payload = platform
    payload["post"].update(content="text notebook-test-key", secret="other-secret", account={"name": "private"})
    scheduled = task()
    observe(scheduled, moltbook)
    stored = repr(db.query("SELECT * FROM moltbook_sources"))
    assert "[redacted]" in stored
    assert all(value not in stored for value in ("notebook-test-key", "other-secret", "private"))
    agent, _ = agent_for([wants(call("moltbook_home")), says("done")], tools=[moltbook.moltbook_home])
    scheduler.run_task(scheduled, agent)
    assert db.query("SELECT seen_count FROM moltbook_sources") == [(1,)]


@pytest.mark.parametrize("name,args,payload,kind", [
    ("moltbook_comments", {"post_id": "post-1"}, {"comments": [{"id": "parent", "content": "parent text",
      "replies": [{"id": "child", "content": "child text"}]}]}, "comment"),
    ("moltbook_search", {"query": "continuity"}, {"results": [{"id": "result", "content": "snippet"}]}, "search_snippet"),
    ("moltbook_feed", {}, {"posts": [{"id": "post", "content": "feed text"}]}, "post"),
    ("moltbook_submolts", {}, {"submolts": [{"name": "agents", "description": "public description"}]}, "submolt"),
])
def test_supported_public_payload_shapes(platform, name, args, payload, kind):
    moltbook, _, target = platform
    target.clear()
    target.update(payload)
    scheduled = task()
    agent, _ = agent_for([wants(call(name, args)), says("done")], tools=[getattr(moltbook, name)])
    scheduler.run_task(scheduled, agent)
    rows = db.query("SELECT kind,remote_id FROM moltbook_sources")
    assert rows and all(row[0] == kind for row in rows)
    if kind == "comment":
        assert {row[1] for row in rows} == {"parent", "child"}


def test_capture_and_lookup_are_bounded(platform):
    moltbook, _, payload = platform
    payload.clear()
    payload["posts"] = [{"id": f"post-{n}", "content": "x" * 4000} for n in range(15)]
    scheduled = task()
    agent, _ = agent_for([wants(call("moltbook_feed")), says("done")], tools=[moltbook.moltbook_feed])
    scheduler.run_task(scheduled, agent)
    rows = db.query("SELECT excerpt FROM moltbook_sources")
    assert len(rows) == 10 and all(len(row[0]) == 3000 for row in rows)
    output = invoke(scheduled, "moltbook_notebook", {"limit": 999, "include_sources": True})
    assert len(output) < 12500
    assert json.loads(output.split("\n", 1)[1])["omitted_for_size"] is True


def test_read_failure_does_not_capture_or_claim_success(platform, monkeypatch):
    from vegapunk import moltbook_notebook
    moltbook, _, payload = platform
    scheduled = task()
    payload["success"] = False
    assert "unsuccessful" in tool_result(observe(scheduled, moltbook))
    assert db.query("SELECT COUNT(*) FROM moltbook_sources") == [(0,)]
    payload.pop("success")
    def broken(*args):
        raise db.StoreError("snapshot write failed")
    monkeypatch.setattr(moltbook_notebook, "capture", broken)
    output = tool_result(observe(scheduled, moltbook))
    assert "failed with StoreError" in output
    assert not output.startswith("Untrusted Moltbook data from GET ")
    assert db.query("SELECT outcome FROM scheduled_run_events ORDER BY created_at") == [("blocked",), ("error",)]


def test_lookup_and_context_isolate_tasks_and_rotated_credentials(platform):
    from vegapunk import moltbook_notebook
    moltbook, path, _ = platform
    one, two = task("one"), task("two")
    observe(one, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    note(one, source)
    assert source in invoke(one, "moltbook_notebook", {"include_sources": True})
    assert source not in invoke(two, "moltbook_notebook", {"include_sources": True})
    assert moltbook_notebook.context(two.id) == ""
    path.write_text(json.dumps({"api_key": "rotated-test-key"}))
    assert source not in invoke(one, "moltbook_notebook", {"include_sources": True})
    assert source not in invoke(one, "moltbook_notebook", {"source_id": source})
    assert moltbook_notebook.context(one.id) == ""
    assert source in moltbook_notebook.format_notebook(one.id)


def test_completion_requires_evidence_and_preserves_original_quote(platform):
    moltbook, _, payload = platform
    scheduled = task()
    observe(scheduled, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    note(scheduled, source, kind="question")
    note_id = db.query("SELECT id FROM moltbook_notes")[0][0]
    args = {"note_id": note_id, "resolution": "The peer provided a qualification.", "source_id": source, "quote": "invented"}
    assert invoke(scheduled, "moltbook_complete_note", args).startswith("Blocked:")
    payload["post"]["content"] = "Receipts do not establish public visibility."
    observe(scheduled, moltbook)
    second = db.query("SELECT id FROM moltbook_sources ORDER BY created_at DESC")[0][0]
    args.update(source_id=second, quote=payload["post"]["content"])
    assert "model-reported" in invoke(scheduled, "moltbook_complete_note", args)
    assert "already recorded" in invoke(scheduled, "moltbook_complete_note", args)
    assert db.query("SELECT status,source_id,quote,completion_source_id FROM moltbook_notes") == [
        ("completed", source, "Durable receipts prevent duplicate replies.", second)]


@pytest.mark.parametrize("extra", [{"confidence": "certain"}, {"kind": "fact"}, {"text": "notebook-test-key"},
                                    {"follow_up_on": "2026-09-30"}, {"kind": "follow_up", "follow_up_on": "tomorrow"}])
def test_invalid_note_inputs_do_not_persist(platform, extra):
    moltbook, _, _ = platform
    scheduled = task()
    observe(scheduled, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    assert tool_result(note(scheduled, source, **extra)).startswith("Blocked:")
    assert db.query("SELECT COUNT(*) FROM moltbook_notes") == [(0,)]


def test_cross_task_revision_rejected_and_original_preserved(platform):
    moltbook, _, _ = platform
    one, two = task("one"), task("two")
    observe(one, moltbook)
    note(one, db.query("SELECT id FROM moltbook_sources WHERE task_id=?", (one.id,))[0][0])
    old = db.query("SELECT id FROM moltbook_notes")[0][0]
    observe(two, moltbook)
    source = db.query("SELECT id FROM moltbook_sources WHERE task_id=?", (two.id,))[0][0]
    assert tool_result(note(two, source, supersedes=old)).startswith("Blocked:")
    assert db.query("SELECT status FROM moltbook_notes") == [("active",)]


def test_five_new_notes_per_run_and_due_context_priority(platform):
    from vegapunk import moltbook_notebook
    from vegapunk.tools import moltbook_notebook as tools
    moltbook, _, _ = platform
    scheduled = task()
    observe(scheduled, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    turns = [wants(call("moltbook_note", {"kind": "hypothesis", "subject": "continuity", "text": f"Interpretation {n}",
                  "source_id": source, "quote": "Durable receipts"})) for n in range(6)]
    agent, _ = agent_for([*turns, says("done")], tools=[tools.moltbook_note])
    scheduler.run_task(scheduled, agent)
    assert db.query("SELECT COUNT(*) FROM moltbook_notes") == [(5,)]
    note(scheduled, source, kind="follow_up", text="Due follow-up", follow_up_on="2026-01-01")
    context = moltbook_notebook.context(scheduled.id)
    notes = json.loads(context.split("\n")[2])
    assert len(notes) == 5 and notes[0]["text"] == "Due follow-up"


def test_human_command_retains_removed_task_history(platform):
    from vegapunk.commands import CommandContext, dispatch
    from tests.fake_provider import session_for
    moltbook, _, _ = platform
    scheduled = task()
    observe(scheduled, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    note(scheduled, source)
    scheduler.remove_task(scheduled.id)
    output = dispatch(f"/schedule notebook {scheduled.id[:8]}", CommandContext(session=session_for())).output
    assert source in output and "hypothesis" in output


def test_indirect_and_expired_execution_cannot_persist(platform):
    from contextvars import copy_context
    from logpose import tool
    from vegapunk import moltbook_actions as actions
    from vegapunk.tools.moltbook_notebook import moltbook_notebook as lookup
    moltbook, _, _ = platform
    scheduled = task()
    contexts = []
    @tool
    def indirect_notebook() -> str:
        contexts.append(copy_context())
        return lookup()
    agent, provider = agent_for([wants(call("indirect_notebook")), says("done")], tools=[indirect_notebook])
    scheduler.run_task(scheduled, agent)
    assert tool_result(provider).startswith("Blocked:")
    with pytest.raises(actions.ActionBlocked):
        contexts[0].run(actions.task_execution, "indirect_notebook")
    assert db.query("SELECT COUNT(*) FROM moltbook_sources") == [(0,)]


def test_schema_seven_upgrade_keeps_existing_data():
    db.execute("UPDATE meta SET value='7' WHERE key='schema_version'")
    db.execute("DROP TABLE moltbook_sources")
    db.execute("DROP TABLE moltbook_notes")
    scheduled = task()
    db.close_connection()
    assert db.query("SELECT value FROM meta WHERE key='schema_version'") == [("8",)]
    assert scheduler.list_tasks()[0].id == scheduled.id
    assert db.query("SELECT COUNT(*) FROM moltbook_sources") == [(0,)]


def test_hundred_active_notes_limit_allows_atomic_revision(platform):
    moltbook, _, _ = platform
    scheduled = task()
    observe(scheduled, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    note(scheduled, source)
    old = db.query("SELECT id FROM moltbook_notes")[0][0]
    # Seed quota rows directly; keep the behavior under test on the real tool path.
    for n in range(99):
        db.execute("INSERT INTO moltbook_notes (id,task_id,credential_tag,run_id,kind,subject,text,confidence,"
                   "source_id,quote,follow_up_on,supersedes,fingerprint,created_at,updated_at) "
                   "SELECT ?,task_id,credential_tag,run_id,kind,subject,text,confidence,source_id,quote,"
                   "follow_up_on,supersedes,?,created_at,updated_at FROM moltbook_notes WHERE id=?",
                   (db.new_id(), str(n), old))
    assert tool_result(note(scheduled, source, text="Beyond quota")).startswith("Blocked:")
    assert "recorded:" in tool_result(note(scheduled, source, text="Revised within quota", supersedes=old))
    assert db.query("SELECT COUNT(*) FROM moltbook_notes WHERE status='active'") == [(100,)]
    assert db.query("SELECT status FROM moltbook_notes WHERE id=?", (old,)) == [("superseded",)]


def test_task_disable_between_invocation_and_transaction_blocks_note(platform, monkeypatch):
    from vegapunk import moltbook_notebook
    moltbook, _, _ = platform
    scheduled = task()
    observe(scheduled, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    original = moltbook_notebook.credentials
    def disable():
        db.execute("UPDATE scheduled_tasks SET enabled=0 WHERE id=?", (scheduled.id,))
        return original()
    monkeypatch.setattr(moltbook_notebook, "credentials", disable)
    assert tool_result(note(scheduled, source)).startswith("Blocked:")
    assert db.query("SELECT COUNT(*) FROM moltbook_notes") == [(0,)]


def test_notebook_sources_and_notes_are_in_database_backup(platform):
    import turso
    moltbook, _, _ = platform
    scheduled = task()
    observe(scheduled, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    note(scheduled, source)
    snapshot = db.backup_now()
    conn = turso.connect(str(snapshot))
    try:
        assert conn.execute("SELECT id FROM moltbook_sources").fetchone()[0] == source
        assert conn.execute("SELECT source_id FROM moltbook_notes").fetchone()[0] == source
    finally:
        conn.close()


def test_historical_source_lookup_by_id_remains_scoped(platform):
    moltbook, _, payload = platform
    one, two = task("one"), task("two")
    observe(one, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    for n in range(21):
        payload["post"]["content"] = f"New snapshot {n}"
        observe(one, moltbook)
    assert source not in invoke(one, "moltbook_notebook", {"include_sources": True, "limit": 20})
    result = invoke(one, "moltbook_notebook", {"source_id": source})
    assert source in result and "Durable receipts prevent duplicate replies." in result
    assert source not in invoke(two, "moltbook_notebook", {"source_id": source})


def test_human_inspection_includes_revision_and_completion_provenance(platform):
    from vegapunk import moltbook_notebook
    moltbook, path, payload = platform
    scheduled = task()
    observe(scheduled, moltbook)
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    note(scheduled, source)
    original = db.query("SELECT id FROM moltbook_notes")[0][0]
    note(scheduled, source, kind="question", supersedes=original)
    revised = db.query("SELECT id FROM moltbook_notes WHERE status='active'")[0][0]
    payload["post"].update(id="post-2", content="Completion evidence.", author={"name": "SecondPeer"})
    observe(scheduled, moltbook)
    second = db.query("SELECT id FROM moltbook_sources ORDER BY created_at DESC")[0][0]
    invoke(scheduled, "moltbook_complete_note", {"note_id": revised, "resolution": "Resolved", "source_id": second,
                                                "quote": "Completion evidence."})
    lookup = json.loads(invoke(scheduled, "moltbook_notebook", {"query": original, "status": "all"}).split("\n", 1)[1])
    assert [entry["id"] for entry in lookup["notes"]] == [original]
    scheduler.remove_task(scheduled.id)
    path.write_text(json.dumps({"api_key": "rotated-test-key"}))
    output = moltbook_notebook.format_notebook(scheduled.id)
    for expected in ("post-1", "post-2", "Peer", "SecondPeer", "/posts/post-1", "Completion evidence.", f"supersedes {original}"):
        assert expected in output
    original_view = moltbook_notebook.format_notebook(original)
    assert original in original_view and revised not in original_view


@pytest.mark.parametrize("response", [None, [], {"post": None}, {"post": {"id": "post-1", "content": []}}])
def test_unknown_empty_shapes_are_not_archived(platform, monkeypatch, response):
    moltbook, _, _ = platform
    monkeypatch.setattr(moltbook, "_get", lambda *args, **kwargs: Response(response))
    observe(task(), moltbook)
    assert db.query("SELECT COUNT(*) FROM moltbook_sources") == [(0,)]

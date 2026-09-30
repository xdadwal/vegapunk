"""Sourced drafts are task-scoped and self-reviewed across separate runs."""

import json

import pytest

from tests.fake_provider import agent_for, call, says, wants
from vegapunk import db, scheduler, task_history


@pytest.fixture
def draft_scope(tmp_path, monkeypatch):
    from vegapunk.tools import moltbook
    from tests.test_moltbook_tool import _credentials
    _credentials(tmp_path, monkeypatch)
    scheduler.add_task("explore", 300, profile="moltbook")
    task = scheduler.list_tasks()[0]
    stamp = db.utcnow()
    import hashlib
    tag = hashlib.sha256(b"moltbook_test_secret").hexdigest()
    db.execute("INSERT INTO moltbook_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("source", task.id, tag, "post", "remote", "/posts/remote", "peer", "Useful public evidence",
                "hash", "read-run", "read-run", stamp, stamp, 1))
    return task, moltbook


def invoke(task, tool, arguments):
    from vegapunk.tools import ALL_TOOLS
    agent, provider = agent_for([wants(call(tool, arguments)), says("done")], tools=ALL_TOOLS)
    scheduler.run_task(task, agent)
    from logpose import ToolResultBlock
    return next(block.content for message in provider.last_request.messages
                for block in message.content if isinstance(block, ToolResultBlock))


def draft(task, **changes):
    args = dict(submolt="agents", title="Useful learning", content="A source-backed interpretation.",
                rationale="Adds a specific observation", source_id="source", quote="public evidence")
    return invoke(task, "moltbook_draft", args | changes)


def test_real_profile_can_create_and_retrieve_sourced_draft(draft_scope):
    task, _ = draft_scope
    result = draft(task)
    draft_id = json.loads(result.split("\n", 1)[1])["draft_id"]
    lookup = invoke(task, "moltbook_drafts", {})
    assert draft_id in lookup and "source-backed interpretation" in lookup
    assert db.query("SELECT COUNT(*) FROM memory") == [(0,)]


def test_duplicate_drafts_are_not_new_attempts(draft_scope):
    task, _ = draft_scope
    draft(task)
    draft(task, rationale="Different rationale for identical content")
    assert db.query("SELECT COUNT(*) FROM moltbook_drafts") == [(1,)]


def test_ready_review_requires_a_later_run(draft_scope):
    from vegapunk import moltbook_actions as actions, moltbook_drafts as drafts
    task, _ = draft_scope
    run_id = task_history.begin(task)
    with actions.execution(task.id, run_id):
        scope = actions.Execution(task.id, run_id, profile_since=task.profile_since)
        result = drafts.save(scope, "agents", "Title", "Content", "Reason", "source", "public evidence")
        draft_id = json.loads(result.split("\n", 1)[1])["draft_id"]
        with pytest.raises(actions.ActionBlocked, match="later run"):
            drafts.review(scope, draft_id, "ready", "Checked source and novelty")
    task_history.finish(task, run_id, "completed", "drafted")
    assert "ready" in invoke(task, "moltbook_review_draft", dict(draft_id=draft_id, verdict="ready", rationale="Re-read source; adds value"))
    assert db.query("SELECT status FROM moltbook_drafts") == [("ready",)]


@pytest.mark.parametrize("changes", [
    {"quote": "not in source"}, {"source_id": "missing"}, {"title": "x" * 301},
    {"content": "x" * 4001}, {"rationale": ""}, {"submolt": "../private"},
    {"content": "moltbook_test_secret"},
])
def test_invalid_drafts_do_not_persist(draft_scope, changes):
    task, _ = draft_scope
    assert "Blocked:" in draft(task, **changes)
    assert db.query("SELECT COUNT(*) FROM moltbook_drafts") == [(0,)]


def test_cross_task_and_profile_epoch_cannot_read_or_review(draft_scope):
    task, _ = draft_scope
    draft(task)
    draft_id = db.query("SELECT id FROM moltbook_drafts")[0][0]
    scheduler.add_task("another", 300, profile="moltbook")
    other = scheduler.list_tasks()[1]
    assert draft_id not in invoke(other, "moltbook_drafts", {})
    assert "Blocked:" in invoke(other, "moltbook_review_draft", dict(draft_id=draft_id, verdict="ready", rationale="review"))
    scheduler.set_profile(task.id, "general")
    scheduler.set_profile(task.id, "moltbook")
    assert draft_id not in invoke(task, "moltbook_drafts", {})
    from vegapunk import moltbook_drafts
    assert draft_id in moltbook_drafts.format_drafts(task.id)


def test_rotated_credential_does_not_retrieve_old_drafts(draft_scope):
    task, moltbook = draft_scope
    draft(task)
    draft_id = db.query("SELECT id FROM moltbook_drafts")[0][0]
    moltbook.config.moltbook_credentials_file.write_text('{"api_key":"rotated"}')
    assert draft_id not in invoke(task, "moltbook_drafts", {})


def test_general_and_interactive_cannot_use_draft_tools(draft_scope):
    from vegapunk.tools import moltbook_drafts
    assert "Blocked:" in moltbook_drafts.moltbook_drafts()
    scheduler.add_task("ordinary", 300)
    task = scheduler.list_tasks()[-1]
    assert "Blocked:" in invoke(task, "moltbook_drafts", {})


def test_draft_cli_and_completion_include_retained_full_ids(draft_scope):
    from tests.fake_provider import session_for
    from tests.test_prompter import _complete
    from vegapunk.commands import CommandContext, dispatch
    task, _ = draft_scope
    draft(task)
    draft_id = db.query("SELECT id FROM moltbook_drafts")[0][0]
    scheduler.remove_task(task.id)
    ctx = CommandContext(session=session_for())
    assert draft_id in dispatch("/schedule drafts", ctx).output
    assert "public evidence" in dispatch(f"/schedule drafts {draft_id}", ctx).output
    assert "drafts" in _complete("/schedule ")
    assert {task.id, draft_id} <= set(_complete("/schedule drafts "))


def test_draft_quotas_and_discard_release_active_capacity(draft_scope):
    from vegapunk import moltbook_actions as actions, moltbook_drafts as drafts
    task, _ = draft_scope
    first_id = ""
    for group in range(4):
        run_id = task_history.begin(task)
        scope = actions.Execution(task.id, run_id, profile_since=task.profile_since)
        for index in range(5):
            result = drafts.save(scope, "agents", f"Title {group}-{index}", "Content", "Reason", "source", "public evidence")
            first_id = first_id or json.loads(result.split("\n", 1)[1])["draft_id"]
        with pytest.raises(actions.ActionBlocked, match="five new drafts"):
            drafts.save(scope, "agents", f"Over quota {group}", "Content", "Reason", "source", "public evidence")
        task_history.finish(task, run_id, "completed", "drafted")
    run_id = task_history.begin(task)
    scope = actions.Execution(task.id, run_id, profile_since=task.profile_since)
    with pytest.raises(actions.ActionBlocked, match="twenty active"):
        drafts.save(scope, "agents", "Another", "Content", "Reason", "source", "public evidence")
    drafts.review(scope, first_id, "discard", "Already covered elsewhere")
    assert "draft_id" in drafts.save(scope, "agents", "Another", "Content", "Reason", "source", "public evidence")
    task_history.finish(task, run_id, "completed", "reviewed")


def test_search_snippet_cannot_support_original_draft(draft_scope):
    task, _ = draft_scope
    db.execute("UPDATE moltbook_sources SET kind='search_snippet' WHERE id='source'")
    assert "search snippet" in draft(task)
    assert db.query("SELECT COUNT(*) FROM moltbook_drafts") == [(0,)]


def test_ready_consumer_rechecks_status_scope_and_credential(draft_scope):
    from vegapunk import moltbook_actions as actions, moltbook_drafts as drafts
    task, moltbook = draft_scope
    draft(task)
    draft_id = db.query("SELECT id FROM moltbook_drafts")[0][0]
    invoke(task, "moltbook_review_draft", dict(draft_id=draft_id, verdict="ready", rationale="Reviewed evidence"))
    run_id = task_history.begin(task)
    scope = actions.Execution(task.id, run_id, profile_since=task.profile_since)
    assert drafts.ready(scope, draft_id)["content"] == "A source-backed interpretation."
    drafts.review(scope, draft_id, "discard", "No longer useful")
    with pytest.raises(actions.ActionBlocked, match="ready draft"):
        drafts.ready(scope, draft_id)
    moltbook.config.moltbook_credentials_file.write_text('{"api_key":"rotated"}')
    with pytest.raises(actions.ActionBlocked, match="belong"):
        drafts.ready(scope, draft_id)
    task_history.finish(task, run_id, "completed", "checked")
    scheduler.set_profile(task.id, "general")
    scheduler.set_profile(task.id, "moltbook")
    fresh = scheduler.list_tasks()[0]
    run_id = task_history.begin(fresh)
    with pytest.raises(actions.ActionBlocked, match="belong"):
        drafts.ready(actions.Execution(task.id, run_id, profile_since=fresh.profile_since), draft_id)
    task_history.finish(fresh, run_id, "completed", "checked")


def test_oversized_draft_lookup_reports_omission_without_partial_entries(draft_scope):
    task, _ = draft_scope
    for index in range(4):
        draft(task, title=f"Draft {index}", content="x" * 4000)
    result = json.loads(invoke(task, "moltbook_drafts", {"limit": 20}).split("\n", 1)[1])
    assert result["omitted_for_size"] is True
    assert 0 < len(result["drafts"]) < 4
    assert all(len(row["content"]) == 4000 for row in result["drafts"])

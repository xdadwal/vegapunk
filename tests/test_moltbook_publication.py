"""Autonomous publication uses real policy, read evidence and durable intents."""

import hashlib
import json

import pytest
import requests

from tests.fake_provider import agent_for, call, says, wants
from tests.test_moltbook_drafts import draft, invoke
from tests.test_moltbook_tool import _credentials, _FakeResponse
from vegapunk import db, scheduler, task_history, moltbook_actions as ledger


@pytest.fixture
def platform(tmp_path, monkeypatch):
    from vegapunk.tools import moltbook, moltbook_actions
    _credentials(tmp_path, monkeypatch)
    monkeypatch.setattr(moltbook_actions, "config", moltbook.config)
    state = {"sent": [], "results": [], "submolt": {"name": "agents", "description": "Public learning"},
             "response": {"success": True, "post": {"id": "published"}}, "remote": None}
    def get(url, **kwargs):
        assert kwargs["allow_redirects"] is False
        if url.endswith("/agents/me"):
            data = {"success": True, "agent": {"id": "account", "name": "Vegapunk"}}
        elif url.endswith("/submolts/agents"):
            data = {"success": True, "submolt": state["submolt"]}
        elif url.endswith("/search"):
            data = {"success": True, "results": state["results"], "has_more": False}
        elif url.endswith("/comments"):
            data = {"success": True, "comments": [{"id": "parent"}]}
        else:
            data = {"success": True, "post": state["remote"] or {"id": "post", "author": {"id": "account"}}}
        return _FakeResponse(data)
    def post(url, **kwargs):
        # There must be durable intent before any remote side effect.
        assert db.query("SELECT state FROM moltbook_actions WHERE state IN ('sending','verifying')")
        state["sent"].append((url, kwargs["json"]))
        if state.get("timeout"):
            raise requests.Timeout("uncertain")
        return _FakeResponse(state["response"])
    monkeypatch.setattr(moltbook, "_get", get)
    monkeypatch.setattr(moltbook_actions, "_get", get)
    monkeypatch.setattr(moltbook_actions, "_post", post)
    scheduler.add_task("Learn from public discussions", 300, profile="moltbook")
    state["task"] = scheduler.list_tasks()[0]
    return state


def enable(state):
    assert hasattr(scheduler, "set_autonomy"), "one-time autonomous policy is missing"
    result = scheduler.set_autonomy(state["task"].id, "on")
    assert "enabled" in result.lower(), result
    state["task"] = scheduler.list_tasks()[0]
    return state["task"]


def create_draft(state, title="Useful learning"):
    task = state["task"]
    stamp = db.utcnow()
    db.execute("INSERT OR REPLACE INTO moltbook_sources VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("source", task.id, hashlib.sha256(b"moltbook_test_secret").hexdigest(), "post", "remote",
                "/posts/remote", "peer", "Useful public evidence", "hash", "read-run", "read-run", stamp, stamp, 1))
    result = draft(task, title=title)
    return json.loads(result.split("\n", 1)[1])["draft_id"]


def publish_run(state, draft_id, *, reads=True, review=True, publish=True):
    turns = []
    if reads:
        title = db.query("SELECT title FROM moltbook_drafts WHERE id=?", (draft_id,))[0][0]
        turns.append(wants(call("moltbook_submolts", {"name": "agents"}),
                           call("moltbook_search", {"query": title, "content_type": "posts", "limit": 5})))
    if review:
        turns.append(wants(call("moltbook_review_draft", {"draft_id": draft_id, "verdict": "ready",
                                                         "rationale": "Read the community and related discussion; a useful new observation."})))
    if publish:
        turns.append(wants(call("moltbook_publish", {"draft_id": draft_id})))
    agent, provider = agent_for([*turns, says("checked")])
    scheduler.run_task(state["task"], agent)
    return provider


def test_autonomy_defaults_off_and_command_is_local_except_initial_identity(platform):
    from tests.fake_provider import session_for
    from tests.test_prompter import _complete
    from vegapunk.commands import CommandContext, dispatch
    assert hasattr(scheduler, "set_autonomy")
    assert db.query("SELECT * FROM moltbook_autonomy") == []
    assert "autonomy" in _complete("/schedule ")
    assert _complete(f"/schedule autonomy {platform['task'].id} ") == ["on", "off"]
    ctx = CommandContext(session=session_for())
    assert "enabled" in dispatch(f"/schedule autonomy {platform['task'].id} on", ctx).output.lower()
    assert platform["sent"] == []


def test_default_and_interactive_publish_denied(platform):
    from vegapunk.tools import REGISTRY
    assert "moltbook_publish" in {tool.name for tool in REGISTRY}
    draft_id = create_draft(platform)
    publish_run(platform, draft_id)
    assert platform["sent"] == []
    from vegapunk.tools.moltbook_publication import moltbook_publish
    assert "Blocked:" in moltbook_publish(draft_id)


def test_policy_changes_require_idle_social_task_and_quarantine_old_drafts(platform):
    task = enable(platform)
    draft_id = create_draft(platform)
    run = task_history.begin(task)
    assert "active run" in scheduler.set_autonomy(task.id, "off")
    task_history.finish(task, run, "completed", "done")
    assert "disabled" in scheduler.set_autonomy(task.id, "off")
    assert draft_id not in invoke(task, "moltbook_drafts", {})
    scheduler.set_profile(task.id, "general")
    assert "moltbook" in scheduler.set_autonomy(task.id, "on").lower()
    assert not db.query("SELECT * FROM moltbook_autonomy")


def test_reviewed_current_read_evidence_sends_documented_payload_once(platform):
    enable(platform)
    draft_id = create_draft(platform)
    publish_run(platform, draft_id)
    assert platform["sent"] == [("https://www.moltbook.com/api/v1/posts", {
        "submolt_name": "agents", "title": "Useful learning", "content": "A source-backed interpretation."})]
    action = ledger.list_actions()[0]
    assert action["kind"] == "post" and action["state"] == "accepted"
    assert db.query("SELECT status FROM moltbook_drafts") == [("published",)]
    assert db.query("SELECT COUNT(*) FROM moltbook_read_receipts") == [(2,)]
    assert "success" in [event.outcome for event in task_history.events(task_history.list_runs(platform["task"].id)[0].id)
                         if event.tool == "moltbook_publish"]
    publish_run(platform, draft_id)
    assert len(platform["sent"]) == 1


@pytest.mark.parametrize("reads,review", [(False, True), (True, False)])
def test_missing_current_checks_or_review_prevents_dispatch(platform, reads, review):
    enable(platform)
    draft_id = create_draft(platform)
    publish_run(platform, draft_id, reads=reads, review=review)
    assert platform["sent"] == []
    assert ledger.list_actions() == []


def test_previous_run_review_and_evidence_cannot_publish(platform):
    enable(platform)
    draft_id = create_draft(platform)
    publish_run(platform, draft_id, publish=False)
    publish_run(platform, draft_id, reads=False, review=False)
    assert platform["sent"] == []


def test_original_post_daily_limit_survives_policy_toggle(platform):
    task = enable(platform)
    publish_run(platform, create_draft(platform))
    scheduler.set_autonomy(task.id, "off")
    enable(platform)
    publish_run(platform, create_draft(platform, "Another learning"))
    assert len(platform["sent"]) == 1


def test_unknown_post_blocks_reply_and_is_never_replayed(platform):
    enable(platform)
    draft_id = create_draft(platform)
    platform["timeout"] = True
    publish_run(platform, draft_id)
    assert ledger.list_actions()[0]["state"] == "unknown"
    # Pass recovery window before attempting another kind of write.
    db.execute("DELETE FROM moltbook_request_backoff")
    invoke(platform["task"], "moltbook_reply", {"post_id": "post", "parent_id": "parent", "content": "A reply"})
    publish_run(platform, draft_id)
    assert len(platform["sent"]) == 1


def test_pending_post_verification_uses_internal_code_once(platform):
    enable(platform)
    draft_id = create_draft(platform)
    platform["response"] = {"success": True, "post": {"id": "published", "verification_status": "pending",
        "verification": {"verification_code": "private-code", "challenge_text": "20 minus 5",
                         "expires_at": db.utcnow_plus(300)}}}
    publish_run(platform, draft_id)
    action = ledger.list_actions()[0]
    assert action["state"] == "pending_verification" and "private-code" not in repr(action)
    platform["response"] = {"success": True, "content_type": "post", "content_id": "published"}
    invoke(platform["task"], "moltbook_verify_post", {"action_id": action["id"], "answer": "15.00"})
    assert platform["sent"][-1][1] == {"verification_code": "private-code", "answer": "15.00"}
    invoke(platform["task"], "moltbook_verify_post", {"action_id": action["id"], "answer": "15.00"})
    assert len(platform["sent"]) == 2
    assert ledger.list_actions()[0]["state"] == "accepted"


def test_exact_read_back_can_recover_unknown_but_absence_does_not_reject(platform):
    enable(platform)
    draft_id = create_draft(platform)
    platform["timeout"] = True
    publish_run(platform, draft_id)
    db.execute("DELETE FROM moltbook_request_backoff")
    action_id = ledger.list_actions()[0]["id"]
    invoke(platform["task"], "moltbook_reconcile", {"action_id": action_id})
    assert ledger.list_actions()[0]["state"] == "unknown"
    platform["results"] = [{"id": "published", "type": "post", "post_id": "published", "title": "Useful learning"}]
    platform["remote"] = {"id": "published", "title": "Useful learning", "content": "A source-backed interpretation.",
                          "author": {"id": "account"}, "submolt": {"name": "agents"}, "verification_status": "verified",
                          "created_at": ledger.list_actions()[0]["created_at"]}
    result = invoke(platform["task"], "moltbook_reconcile", {"action_id": action_id})
    assert ledger.list_actions()[0]["state"] == "read_back_confirmed"
    assert "public visibility" in result and len(platform["sent"]) == 1


def test_rotated_credentials_cannot_publish_or_verify_old_intents(platform):
    enable(platform)
    draft_id = create_draft(platform)
    from vegapunk.tools import moltbook
    moltbook.config.moltbook_credentials_file.write_text('{"api_key":"rotated"}')
    publish_run(platform, draft_id)
    assert platform["sent"] == []


def test_autonomous_reply_needs_no_separate_grant(platform):
    enable(platform)
    platform["response"] = {"success": True, "comment": {"id": "reply"}}
    invoke(platform["task"], "moltbook_reply", {"post_id": "post", "parent_id": "parent", "content": "A useful reply"})
    assert len(platform["sent"]) == 1
    assert ledger.list_actions()[0]["state"] == "accepted"


def test_expired_post_challenge_closes_without_reposting(platform):
    enable(platform)
    platform["response"] = {"success": True, "post": {"id": "published", "verification": {
        "verification_code": "private-code", "challenge_text": "20 minus 5", "expires_at": db.utcnow_plus(-1)}}}
    publish_run(platform, create_draft(platform))
    action = ledger.list_actions()[0]
    invoke(platform["task"], "moltbook_verify_post", {"action_id": action["id"], "answer": "15.00"})
    assert ledger.list_actions()[0]["state"] == "verification_expired"
    assert len(platform["sent"]) == 1


def test_action_context_keeps_challenge_but_hides_rotated_epoch(platform):
    enable(platform)
    platform["response"] = {"success": True, "post": {"id": "published", "verification": {
        "verification_code": "private-code", "challenge_text": "20 minus 5", "expires_at": db.utcnow_plus(300)}}}
    publish_run(platform, create_draft(platform))
    context = ledger.context(platform["task"].id)
    assert "20 minus 5" in context and "private-code" not in context
    from vegapunk.tools import moltbook
    moltbook.config.moltbook_credentials_file.write_text('{"api_key":"rotated"}')
    assert "20 minus 5" not in ledger.context(platform["task"].id)


def test_fresh_rule_change_after_review_prevents_intent(platform, monkeypatch):
    from vegapunk.tools import moltbook_actions
    enable(platform)
    original = moltbook_actions._get
    def changed(url, **kwargs):
        response = original(url, **kwargs)
        if url.endswith("/submolts/agents"):
            return _FakeResponse({"success": True, "submolt": {"name": "agents", "description": "Changed rules"}})
        return response
    monkeypatch.setattr(moltbook_actions, "_get", changed)
    publish_run(platform, create_draft(platform))
    assert platform["sent"] == [] and ledger.list_actions() == []


@pytest.mark.parametrize("field,value", [("author", {"id": "other"}), ("content", "Different content"),
                                         ("submolt", {"name": "other"})])
def test_reconcile_rejects_partial_matches(platform, field, value):
    enable(platform)
    platform["timeout"] = True
    publish_run(platform, create_draft(platform))
    db.execute("DELETE FROM moltbook_request_backoff")
    platform["results"] = [{"type": "post", "id": "published"}]
    platform["remote"] = {"id": "published", "title": "Useful learning", "content": "A source-backed interpretation.",
                          "author": {"id": "account"}, "submolt": {"name": "agents"}}
    platform["remote"][field] = value
    invoke(platform["task"], "moltbook_reconcile", {"action_id": ledger.list_actions()[0]["id"]})
    assert ledger.list_actions()[0]["state"] == "unknown" and len(platform["sent"]) == 1


@pytest.mark.parametrize("private,duplicate", [(True, False), (False, True)])
def test_obvious_private_or_duplicate_content_denied(platform, private, duplicate):
    enable(platform)
    if private:
        platform["submolt"]["is_private"] = True
    if duplicate:
        platform["results"] = [{"type": "post", "id": "existing", "title": "Useful learning", "content": "A source-backed interpretation."}]
    publish_run(platform, create_draft(platform))
    assert not platform["sent"] and not ledger.list_actions()


def test_pending_readback_keeps_internal_challenge(platform):
    enable(platform)
    platform["response"] = {"success": True, "post": {"id": "published", "verification": {
        "verification_code": "private-code", "challenge_text": "20 minus 5", "expires_at": db.utcnow_plus(300)}}}
    publish_run(platform, create_draft(platform))
    action = ledger.list_actions()[0]
    platform["remote"] = {"id": "published", "title": "Useful learning", "content": "A source-backed interpretation.",
                          "author": {"id": "account"}, "submolt": {"name": "agents"}, "verification_status": "pending"}
    invoke(platform["task"], "moltbook_reconcile", {"action_id": action["id"]})
    assert ledger.list_actions()[0]["state"] == "pending_verification"
    assert db.query("SELECT verification_code FROM moltbook_actions") == [("private-code",)]


@pytest.mark.parametrize("stamp", [None, "2020-01-01T00:00:00Z"])
def test_unknown_readback_needs_recent_timestamp(platform, stamp):
    enable(platform)
    platform["timeout"] = True
    publish_run(platform, create_draft(platform))
    db.execute("DELETE FROM moltbook_request_backoff")
    platform["results"] = [{"type": "post", "id": "published"}]
    platform["remote"] = {"id": "published", "title": "Useful learning", "content": "A source-backed interpretation.",
                          "author": {"id": "account"}, "submolt": {"name": "agents"}, "verification_status": "verified"}
    if stamp:
        platform["remote"]["created_at"] = stamp
    invoke(platform["task"], "moltbook_reconcile", {"action_id": ledger.list_actions()[0]["id"]})
    assert ledger.list_actions()[0]["state"] == "unknown"


def test_autonomous_reply_expiry_closes_without_dispatch(platform):
    enable(platform)
    platform["response"] = {"success": True, "comment": {"id": "reply", "verification": {
        "verification_code": "private-code", "challenge_text": "20 minus 5", "expires_at": db.utcnow_plus(-1)}}}
    invoke(platform["task"], "moltbook_reply", {"post_id": "post", "parent_id": "parent", "content": "Useful reply"})
    invoke(platform["task"], "moltbook_verify_reply", {"action_id": ledger.list_actions()[0]["id"], "answer": "15.00"})
    assert ledger.list_actions()[0]["state"] == "verification_expired" and len(platform["sent"]) == 1


def test_multiple_exact_remote_matches_remain_unknown(platform, monkeypatch):
    from vegapunk.tools import moltbook_actions
    enable(platform)
    platform["timeout"] = True
    publish_run(platform, create_draft(platform))
    db.execute("DELETE FROM moltbook_request_backoff")
    action = ledger.list_actions()[0]
    platform["results"] = [{"type": "post", "id": "first"}, {"type": "post", "id": "second"}]
    original = moltbook_actions._get
    def get(url, **kwargs):
        if url.endswith("/posts/first") or url.endswith("/posts/second"):
            return _FakeResponse({"success": True, "post": {
                "id": url.rsplit("/", 1)[1], "title": "Useful learning", "content": "A source-backed interpretation.",
                "author": {"id": "account"}, "submolt": {"name": "agents"}, "verification_status": "verified",
                "created_at": action["created_at"]}})
        return original(url, **kwargs)
    monkeypatch.setattr(moltbook_actions, "_get", get)
    invoke(platform["task"], "moltbook_reconcile", {"action_id": action["id"]})
    assert ledger.list_actions()[0]["state"] == "unknown"


def test_reconcile_rejects_echoed_credential_identifier(platform, monkeypatch):
    from vegapunk.tools import moltbook_actions
    enable(platform)
    platform["timeout"] = True
    publish_run(platform, create_draft(platform))
    db.execute("DELETE FROM moltbook_request_backoff")
    key = "moltbook_test_secret"
    platform["results"] = [{"type": "post", "id": key}]
    original = moltbook_actions._get
    def get(url, **kwargs):
        assert not url.endswith("/posts/" + key), "credential-bearing candidate must not be fetched"
        return original(url, **kwargs)
    monkeypatch.setattr(moltbook_actions, "_get", get)
    result = invoke(platform["task"], "moltbook_reconcile", {"action_id": ledger.list_actions()[0]["id"]})
    assert key not in result and key not in repr(ledger.list_actions())
    assert "Blocked:" in result


def test_migrated_legacy_reply_challenge_remains_in_scoped_context(platform):
    task = platform["task"]
    ledger.grant(task.id, "account")
    platform["response"] = {"success": True, "comment": {"id": "reply", "verification": {
        "verification_code": "private-code", "challenge_text": "20 minus 5", "expires_at": db.utcnow_plus(300)}}}
    invoke(task, "moltbook_reply", {"post_id": "post", "parent_id": "parent", "content": "Useful reply"})
    # Schema v12 actions had no credential/epoch columns. Reopen a populated v12
    # table so the real migration supplies those conservative blank defaults.
    import sqlite3
    db.close_connection()
    conn = sqlite3.connect(db.db_path())
    conn.execute("ALTER TABLE moltbook_actions RENAME TO legacy_actions")
    columns = [row[1] for row in conn.execute("PRAGMA table_info(legacy_actions)").fetchall()
               if row[1] not in ("kind", "draft_id", "credential_tag", "profile_since", "title", "submolt", "checks_json")]
    conn.execute("CREATE TABLE moltbook_actions AS SELECT " + ",".join(columns) + " FROM legacy_actions")
    conn.execute("UPDATE meta SET value='12' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    db.get_connection()
    context = ledger.context(task.id)
    assert "20 minus 5" in context and "private-code" not in context
    db.execute("UPDATE moltbook_permissions SET account_id='other' WHERE task_id=?", (task.id,))
    assert "20 minus 5" not in ledger.context(task.id)


def test_concurrent_publication_calls_reserve_only_one_post(platform, monkeypatch):
    from threading import Barrier
    from vegapunk.tools import moltbook_actions
    enable(platform)
    draft_id = create_draft(platform)
    original = moltbook_actions._get
    barrier = Barrier(2)
    def get(url, **kwargs):
        if url.endswith("/agents/me"):
            barrier.wait(timeout=5)
        return original(url, **kwargs)
    monkeypatch.setattr(moltbook_actions, "_get", get)
    agent, _ = agent_for([
        wants(call("moltbook_submolts", {"name": "agents"}),
              call("moltbook_search", {"query": "Useful learning", "content_type": "posts", "limit": 5})),
        wants(call("moltbook_review_draft", {"draft_id": draft_id, "verdict": "ready", "rationale": "Reviewed public evidence"})),
        wants(call("moltbook_publish", {"draft_id": draft_id}),
              call("moltbook_publish", {"draft_id": draft_id})), says("done")])
    agent.max_concurrent_tools = 2
    scheduler.run_task(platform["task"], agent)
    assert len(platform["sent"]) == 1 and len(ledger.list_actions()) == 1

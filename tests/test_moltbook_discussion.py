"""Autonomous discussion writes retain real authorization and durable receipts."""

import pytest

from tests.fake_provider import agent_for, call, says, wants
from tests.test_moltbook_publication import platform, enable
from vegapunk import db, scheduler, moltbook_actions as ledger


def read_calls():
    return [call("moltbook_post", {"post_id": "post"}),
            call("moltbook_comments", {"post_id": "post"}),
            call("moltbook_submolts", {"name": "agents"})]


def run(state, parent="", reads=True):
    responses = [wants(*read_calls())] if reads else []
    responses += [wants(call("moltbook_comment", {"post_id": "post", "content": "Useful contribution", "parent_id": parent})), says("done")]
    agent, _ = agent_for(responses)
    scheduler.run_task(state["task"], agent)


@pytest.mark.parametrize("parent", ["", "parent"])
def test_autonomous_comment_on_peer_post_has_durable_intent(platform, parent):
    platform["remote"] = {"id": "post", "author": {"id": "peer"}, "submolt": {"name": "agents"}}
    platform["response"] = {"success": True, "comment": {"id": "comment"}}
    enable(platform)
    run(platform, parent)
    assert platform["sent"] == [("https://www.moltbook.com/api/v1/posts/post/comments",
                                 {"content": "Useful contribution", **({"parent_id": "parent"} if parent else {})})]
    action = ledger.list_actions()[0]
    assert action["state"] == "accepted" and action["kind"] == "discussion"
    assert action["parent_id"] == parent and action["remote_id"] == "comment"
    db.close_connection()
    run(platform, parent)
    assert len(platform["sent"]) == 1


def test_legacy_grant_cannot_comment_on_peer_post(platform):
    platform["remote"] = {"id": "post", "author": {"id": "peer"}, "submolt": {"name": "agents"}}
    ledger.grant(platform["task"].id, "account")
    run(platform)
    assert platform["sent"] == [] and ledger.list_actions() == []


def test_comment_requires_current_run_reads(platform):
    platform["remote"] = {"id": "post", "author": {"id": "peer"}, "submolt": {"name": "agents"}}
    enable(platform)
    run(platform, reads=False)
    assert platform["sent"] == [] and ledger.list_actions() == []


@pytest.mark.parametrize("omit", ["moltbook_post", "moltbook_comments", "moltbook_submolts"])
def test_each_required_read_must_be_delivered(platform, omit):
    platform["remote"] = {"id": "post", "author": {"id": "peer"}, "submolt": {"name": "agents"}}
    enable(platform)
    agent, _ = agent_for([wants(*(item for item in read_calls() if item.name != omit)),
                         wants(call("moltbook_comment", {"post_id": "post", "content": "Useful contribution"})), says("done")])
    scheduler.run_task(platform["task"], agent)
    assert platform["sent"] == [] and ledger.list_actions() == []


@pytest.mark.parametrize("parent,private", [("missing", False), ("", True)])
def test_invalid_parent_or_private_submolt_cannot_dispatch(platform, parent, private):
    platform["remote"] = {"id": "post", "author": {"id": "peer"}, "submolt": {"name": "agents"}}
    platform["submolt"]["is_private"] = private
    enable(platform)
    run(platform, parent)
    assert platform["sent"] == [] and ledger.list_actions() == []


def test_discussion_and_own_reply_share_one_run_budget(platform, monkeypatch):
    from vegapunk.tools import moltbook_actions as tools
    platform["remote"] = {"id": "post", "author": {"id": "account"}, "submolt": {"name": "agents"}}
    platform["response"] = {"success": True, "comment": {"id": "comment"}}
    enable(platform)
    post = tools._post
    def clear_other_refusal_reasons(url, **kwargs):
        response = post(url, **kwargs)
        # Isolate the per-run quota from duplicate/thread/account cooldowns.
        db.execute("UPDATE moltbook_actions SET post_id='earlier',created_at=?",
                   (db.stamp_plus(db.utcnow(), -25200),))
        return response
    monkeypatch.setattr(tools, "_post", clear_other_refusal_reasons)
    agent, _ = agent_for([wants(*read_calls()),
        wants(call("moltbook_comment", {"post_id": "post", "content": "Useful contribution"})),
        wants(call("moltbook_reply", {"post_id": "post", "parent_id": "parent", "content": "Another reply"})), says("done")])
    scheduler.run_task(platform["task"], agent)
    assert len(platform["sent"]) == 1 and len(ledger.list_actions()) == 1


def test_prior_discussions_count_toward_shared_daily_budget(platform):
    platform["remote"] = {"id": "post", "author": {"id": "peer"}, "submolt": {"name": "agents"}}
    enable(platform)
    stamp = db.stamp_plus(db.utcnow(), -120)
    for index in range(3):
        db.execute("INSERT INTO moltbook_actions "
                   "(id,task_id,run_id,account_id,last_run_id,tool_call_id,post_id,parent_id,content,content_hash,state,created_at,updated_at,kind) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (db.new_id(), platform["task"].id, "earlier-run", "account", "earlier-run", "earlier-call",
                    f"earlier-post-{index}", "", "Earlier contribution", "hash", "accepted", stamp, stamp, "discussion"))
    run(platform)
    assert platform["sent"] == [] and len(ledger.list_actions()) == 3


def test_legacy_grant_cannot_verify_discussion_after_autonomy_removed(platform):
    from tests.test_moltbook_drafts import invoke
    platform["remote"] = {"id": "post", "author": {"id": "peer"}, "submolt": {"name": "agents"}}
    platform["response"] = {"success": True, "comment": {"id": "comment", "verification": {
        "verification_code": "internal-code", "challenge_text": "10 + 5", "expires_at": "2099-01-01T00:00:00Z"}}}
    enable(platform)
    run(platform)
    receipt = ledger.list_actions()[0]
    db.execute("DELETE FROM moltbook_autonomy WHERE task_id=?", (platform["task"].id,))
    ledger.grant(platform["task"].id, "account")
    invoke(platform["task"], "moltbook_verify_comment", {"action_id": receipt["id"], "answer": "15.00"})
    assert len(platform["sent"]) == 1 and ledger.list_actions()[0]["state"] == "pending_verification"


def test_discussion_challenge_uses_single_scoped_verification(platform):
    from tests.test_moltbook_drafts import invoke
    platform["remote"] = {"id": "post", "author": {"id": "peer"}, "submolt": {"name": "agents"}}
    platform["response"] = {"success": True, "comment": {"id": "comment", "verification": {
        "verification_code": "internal-code", "challenge_text": "10 + 5", "expires_at": "2099-01-01T00:00:00Z"}}}
    enable(platform)
    run(platform)
    receipt = ledger.list_actions()[0]
    assert receipt["state"] == "pending_verification"
    platform["response"] = {"success": True, "content_type": "comment", "content_id": "comment"}
    invoke(platform["task"], "moltbook_verify_reply", {"action_id": receipt["id"], "answer": "15.00"})
    assert len(platform["sent"]) == 1  # legacy verifier cannot handle this kind
    invoke(platform["task"], "moltbook_verify_comment", {"action_id": receipt["id"], "answer": "15.00"})
    assert platform["sent"][1] == ("https://www.moltbook.com/api/v1/verify",
                                  {"verification_code": "internal-code", "answer": "15.00"})
    assert ledger.list_actions()[0]["state"] == "accepted"
    invoke(platform["task"], "moltbook_verify_comment", {"action_id": receipt["id"], "answer": "15.00"})
    assert len(platform["sent"]) == 2


def test_revocation_after_intent_prevents_legacy_grant_fallback(platform, monkeypatch):
    from vegapunk.tools import moltbook_actions as tools
    platform["remote"] = {"id": "post", "author": {"id": "peer"}, "submolt": {"name": "agents"}}
    enable(platform)
    ledger.grant(platform["task"].id, "account")
    reserve = ledger.reserve
    def reserve_then_revoke(*args, **kwargs):
        action_id = reserve(*args, **kwargs)
        db.execute("DELETE FROM moltbook_autonomy WHERE task_id=?", (platform["task"].id,))
        return action_id
    monkeypatch.setattr(ledger, "reserve", reserve_then_revoke)
    run(platform)
    assert platform["sent"] == []
    assert ledger.list_actions()[0]["state"] == "rejected"
    assert "no HTTP request sent" in ledger.list_actions()[0]["note"]
    assert "Blocked:" in tools.moltbook_comment("post", "Interactive cannot bypass policy")


def test_unknown_comment_is_never_replayed(platform):
    platform["remote"] = {"id": "post", "author": {"id": "peer"}, "submolt": {"name": "agents"}}
    platform["timeout"] = True
    enable(platform)
    run(platform)
    assert len(platform["sent"]) == 1 and ledger.list_actions()[0]["state"] == "unknown"
    run(platform)
    assert len(platform["sent"]) == 1


@pytest.mark.parametrize("bad", ["comments", "submolt", "truncated"])
def test_unusable_delivered_reads_cannot_be_replaced_by_hidden_preflight(platform, monkeypatch, bad):
    from dataclasses import replace
    from tests.test_moltbook_tool import _FakeResponse
    from vegapunk.tools import moltbook
    platform["remote"] = {"id": "post", "author": {"id": "peer"}, "submolt": {"name": "agents"}}
    enable(platform)
    get = moltbook._get
    def malformed(url, **kwargs):
        if bad == "comments" and url.endswith("/comments"):
            return _FakeResponse({"success": True, "comments": [None]})
        if bad == "submolt" and url.endswith("/submolts/agents"):
            return _FakeResponse({"success": True, "submolt": {"name": "unrelated", "rules": "Other rules"}})
        return get(url, **kwargs)
    monkeypatch.setattr(moltbook, "_get", malformed)
    if bad == "truncated":
        monkeypatch.setattr(moltbook, "config", replace(moltbook.config, output_char_cap=50))
    run(platform)
    assert platform["sent"] == [] and ledger.list_actions() == []


def test_concurrent_discussion_calls_reserve_only_one_attempt(platform):
    platform["remote"] = {"id": "post", "author": {"id": "peer"}, "submolt": {"name": "agents"}}
    platform["response"] = {"success": True, "comment": {"id": "comment"}}
    enable(platform)
    agent, _ = agent_for([wants(*read_calls()),
                         wants(call("moltbook_comment", {"post_id": "post", "content": "Useful root"}),
                               call("moltbook_comment", {"post_id": "post", "content": "Useful reply", "parent_id": "parent"})),
                         says("done")])
    scheduler.run_task(platform["task"], agent)
    assert len(platform["sent"]) == 1 and len(ledger.list_actions()) == 1

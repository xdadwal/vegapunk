"""Scoped replies use real gates/storage with a deterministic HTTP boundary."""

import json
from dataclasses import replace

import pytest
import requests

from vegapunk import db, scheduler
from tests.fake_provider import agent_for, call, says, wants


class Response:
    def __init__(self, data, status=200, headers=None):
        self.data, self.status_code, self.headers = data, status, headers or {}
    def json(self):
        return self.data


@pytest.fixture
def platform(tmp_path, monkeypatch):
    from vegapunk.tools import moltbook_actions as tools

    path = tmp_path / "credentials.json"
    path.write_text(json.dumps({"api_key": "test-secret"}))
    monkeypatch.setattr(tools, "config", replace(tools.config, moltbook_credentials_file=path))
    sent = []

    def get(url, **kwargs):
        assert url.startswith("https://www.moltbook.com/api/v1/")
        assert kwargs["allow_redirects"] is False
        if url.endswith("/agents/me"):
            return Response({"success": True, "agent": {"id": "account", "name": "Vegapunk"}})
        if url.endswith("/comments"):
            return Response({"comments": [{"id": name} for name in ("parent", "another", "other", "third")]})
        return Response({"success": True, "post": {"id": "post", "author": {"id": "account"}}})

    def post(url, **kwargs):
        sent.append((url, kwargs))
        assert kwargs["allow_redirects"] is False
        return Response({"success": True, "comment": {"id": "remote-comment"}})

    monkeypatch.setattr(tools, "_get", get)
    monkeypatch.setattr(tools, "_post", post)
    return tools, sent


def task(prompt="explore"):
    scheduler.add_task(prompt, 1800)
    return scheduler.list_tasks()[-1]


def run_reply(task, tools, parent="parent", content="A considered reply."):
    agent, _ = agent_for(
        [wants(call("moltbook_reply", {"post_id": "post", "parent_id": parent, "content": content})), says("checked")],
        tools=[tools.moltbook_reply],
    )
    return scheduler.run_task(task, agent)


def test_default_denied_then_scoped_reply_runs_through_real_gate(platform):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    scheduled = task()
    run_reply(scheduled, tools)
    assert sent == []
    actions.grant(scheduled.id, "account")
    run_reply(scheduled, tools)
    assert len(sent) == 1
    receipt = actions.list_actions(scheduled.id)[0]
    assert receipt["state"] == "accepted"
    assert receipt["remote_id"] == "remote-comment"
    assert "test-secret" not in repr(receipt)


def test_scope_does_not_leak_to_next_task_or_direct_calls(platform):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    one, two = task("one"), task("two")
    actions.grant(one.id, "account")
    run_reply(one, tools)
    run_reply(two, tools, parent="another")
    assert "Blocked:" in tools.moltbook_reply("post", "third", "hello")
    assert len(sent) == 1


def test_duplicate_reply_never_resends_after_reopen(platform):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    run_reply(scheduled, tools)
    db.close_connection()
    run_reply(scheduled, tools, content="changed draft, same parent")
    assert len(sent) == 1


def test_timeout_is_unknown_and_blocks_account_writes(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")

    def timeout(url, **kwargs):
        sent.append(url)
        raise requests.Timeout("Bearer test-secret")
    monkeypatch.setattr(tools, "_post", timeout)
    run_reply(scheduled, tools)
    assert actions.list_actions()[0]["state"] == "unknown"
    assert "test-secret" not in repr(actions.list_actions())
    run_reply(scheduled, tools, parent="other")
    assert len(sent) == 1


def test_other_authors_post_is_refused_before_post(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    original = tools._get
    monkeypatch.setattr(tools, "_get", lambda url, **kwargs:
                        Response({"post": {"id": "post", "author": {"id": "other"}}})
                        if "/posts/" in url else original(url, **kwargs))
    run_reply(scheduled, tools)
    assert sent == []


def test_revocation_prevents_future_calls(platform):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    actions.revoke(scheduled.id)
    run_reply(scheduled, tools)
    assert sent == []


def test_two_batched_replies_reserve_only_one(platform):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    agent, _ = agent_for(
        [wants(*[call("moltbook_reply", {"post_id": "post", "parent_id": p, "content": "reply"})
                  for p in ("parent", "another")]), says("done")], tools=[tools.moltbook_reply],
    )
    scheduler.run_task(scheduled, agent)
    assert len(sent) == 1
    assert len(actions.list_actions()) == 1


@pytest.mark.parametrize("response,state", [
    (Response({}, 302, {"Location": "https://evil.example"}), "unknown"),
    (Response({}, 500), "unknown"),
    (Response({}, 403), "rejected"),
    (Response(["malformed"]), "unknown"),
    (Response({"success": True, "comment": {"id": "remote", "verification_required": True}}), "unknown"),
    (Response({"success": True, "comment": {"id": "remote", "verification_status": "failed"}}), "unknown"),
])
def test_ambiguous_and_rejected_responses_never_claim_acceptance(platform, monkeypatch, response, state):
    from vegapunk import moltbook_actions as actions
    tools, _ = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    monkeypatch.setattr(tools, "_post", lambda *a, **k: response)
    run_reply(scheduled, tools)
    assert actions.list_actions()[0]["state"] == state


def challenge_response():
    return Response({"success": True, "comment": {"id": "remote-comment", "verification": {
        "verification_code": "internal-code", "challenge_text": "20 minus 5 test-secret",
        "expires_at": "2099-01-01T00:00:00Z",
    }}})


def test_verification_uses_stored_code_and_matching_receipt(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions, task_history
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    monkeypatch.setattr(tools, "_post", lambda *a, **k: challenge_response())
    run_reply(scheduled, tools)
    receipt = actions.list_actions()[0]
    assert receipt["state"] == "pending_verification"
    assert "test-secret" not in repr(receipt)
    assert "internal-code" not in actions.context(scheduled.id)
    assert task_history.list_runs()[0].status == "partial"

    def verify(url, **kwargs):
        sent.append((url, kwargs))
        assert url.endswith("/verify")
        assert kwargs["json"] == {"verification_code": "internal-code", "answer": "15.00"}
        return Response({"success": True, "content_type": "comment", "content_id": "remote-comment"})
    monkeypatch.setattr(tools, "_post", verify)
    agent, _ = agent_for([wants(call("moltbook_verify_reply", {"action_id": receipt["id"], "answer": "15.00"})), says("verified")],
                         tools=[tools.moltbook_verify_reply])
    scheduler.run_task(scheduled, agent)
    assert len(sent) == 1
    assert actions.list_actions()[0]["state"] == "accepted"
    assert task_history.list_runs()[0].status == "success"


def test_wrong_task_cannot_verify_another_tasks_action(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    one, two = task("one"), task("two")
    actions.grant(one.id, "account")
    actions.grant(two.id, "account")
    monkeypatch.setattr(tools, "_post", lambda *a, **k: challenge_response())
    run_reply(one, tools)
    action_id = actions.list_actions()[0]["id"]
    monkeypatch.setattr(tools, "_post", lambda *a, **k: pytest.fail("must not send"))
    agent, _ = agent_for([wants(call("moltbook_verify_reply", {"action_id": action_id, "answer": "15.00"})), says("blocked")],
                         tools=[tools.moltbook_verify_reply])
    scheduler.run_task(two, agent)
    assert actions.list_actions()[0]["state"] == "pending_verification"


def test_receipt_storage_failure_preserves_intent_and_blocks_retry(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    def fail(*a, **k):
        raise db.StoreError("unavailable")
    monkeypatch.setattr(actions, "complete", fail)
    run_reply(scheduled, tools)
    assert actions.list_actions()[0]["state"] == "sending"
    run_reply(scheduled, tools)
    assert len(sent) == 1


def test_grant_and_reconcile_commands_are_human_only(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions
    from vegapunk.commands import CommandContext, dispatch
    from tests.fake_provider import session_for
    tools, sent = platform
    scheduled = task()
    ctx = CommandContext(session=session_for())
    assert "Granted" in dispatch(f"/schedule grant {scheduled.id[:8]} moltbook.reply_own", ctx).output
    assert "account" in dispatch("/schedule permissions", ctx).output
    monkeypatch.setattr(tools, "_post", lambda *a, **k: Response({}, 500))
    run_reply(scheduled, tools)
    receipt = actions.list_actions()[0]
    assert "unknown" in dispatch("/schedule actions", ctx).output
    assert "Reconciled" in dispatch(f"/schedule resolve-action {receipt['id'][:8]} rejected", ctx).output
    assert actions.list_actions()[0]["state"] == "rejected"
    assert "Revoked" in dispatch(f"/schedule revoke {scheduled.id[:8]} moltbook.reply_own", ctx).output


def test_limits_and_rate_backoff_are_account_wide(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions, task_history
    _, _ = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    stamp = "2090-01-01T00:00:00.000000Z"
    monkeypatch.setattr(db, "utcnow", lambda: stamp)
    for i in range(3):
        stamp = f"2090-01-01T00:0{i}:00.000000Z"
        run_id = task_history.begin(scheduled)
        scope = actions.Execution(scheduled.id, run_id)
        action_id = actions.reserve(scope, "account", f"post{i}", "parent", "hello")
        actions.complete(action_id, "sending", "accepted", remote_id=f"remote{i}")
        task_history.finish(scheduled, run_id, "success", "done")
    run_id = task_history.begin(scheduled)
    with pytest.raises(actions.ActionBlocked, match="rolling day"):
        actions.reserve(actions.Execution(scheduled.id, run_id), "account", "post4", "parent", "hello")


def test_cooldown_and_server_backoff(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions, task_history
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    monkeypatch.setattr(tools, "_post", lambda *a, **k: Response({}, 429, {"Retry-After": "86400"}))
    run_reply(scheduled, tools)
    assert actions.list_actions()[0]["state"] == "rejected"
    run_id = task_history.begin(scheduled)
    scope = actions.Execution(scheduled.id, run_id)
    with pytest.raises(actions.ActionBlocked, match="rate-limited"):
        actions.reserve(scope, "account", "different-post", "parent", "hello")
    db.execute("DELETE FROM moltbook_backoff")
    with pytest.raises(actions.ActionBlocked, match="six-hour"):
        actions.reserve(scope, "account", "post", "another", "hello")


def test_account_cooldown_applies_across_threads(platform):
    from vegapunk import moltbook_actions as actions, task_history
    scheduled = task()
    actions.grant(scheduled.id, "account")
    first = task_history.begin(scheduled)
    action_id = actions.reserve(actions.Execution(scheduled.id, first), "account", "post", "parent", "hello")
    actions.complete(action_id, "sending", "accepted", remote_id="remote")
    task_history.finish(scheduled, first, "success", "done")
    second = task_history.begin(scheduled)
    with pytest.raises(actions.ActionBlocked, match="account cooldown"):
        actions.reserve(actions.Execution(scheduled.id, second), "account", "another-post", "another-parent", "hello")


def test_registration_and_interactive_approval_cannot_bypass_scope(platform):
    from vegapunk.tools import GUARDED
    from vegapunk import loop
    from vegapunk.approval import Decision

    class AlwaysAllow:
        def approve(self, *args):
            return Decision(allow=True)
    tools, sent = platform
    assert {"moltbook_reply", "moltbook_verify_reply"} <= GUARDED
    agent, _ = agent_for([wants(call("moltbook_reply", {"post_id": "post", "parent_id": "parent", "content": "hello"})), says("done")],
                         tools=[tools.moltbook_reply], approver=AlwaysAllow())
    loop.run(agent, "reply")
    assert sent == []


def test_another_tool_cannot_call_reply_with_inherited_context(platform):
    from logpose import tool
    from vegapunk import moltbook_actions as actions
    tools, sent = platform

    @tool
    def indirect_reply() -> str:
        """Attempt a nested write."""
        return tools.moltbook_reply("post", "parent", "hello")

    scheduled = task()
    actions.grant(scheduled.id, "account")
    agent, _ = agent_for([wants(call("indirect_reply")), says("done")], tools=[indirect_reply])
    scheduler.run_task(scheduled, agent)
    assert sent == []
    assert actions.list_actions() == []


def test_revoked_during_preflight_cannot_reserve(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    original = tools._get
    def get(url, **kwargs):
        if url.endswith("/comments"):
            actions.revoke(scheduled.id)
        return original(url, **kwargs)
    monkeypatch.setattr(tools, "_get", get)
    run_reply(scheduled, tools)
    assert sent == [] and actions.list_actions() == []


def test_timed_out_thread_cannot_reserve_after_run_ends(platform, monkeypatch):
    import threading
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    original_get, original_reserve = tools._get, actions.reserve

    def get(url, **kwargs):
        if url.endswith("/comments"):
            started.set()
            assert release.wait(5)
        return original_get(url, **kwargs)

    def reserve(*args, **kwargs):
        try:
            return original_reserve(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(tools, "_get", get)
    monkeypatch.setattr(actions, "reserve", reserve)
    agent, _ = agent_for([wants(call("moltbook_reply", {"post_id": "post", "parent_id": "parent", "content": "hello"})), says("done")],
                         tools=[tools.moltbook_reply])
    agent.tool_timeout = 0.1
    try:
        scheduler.run_task(scheduled, agent)
        assert started.is_set()
    finally:
        release.set()
    assert finished.wait(3)
    assert sent == [] and actions.list_actions() == []


def test_one_verification_attempt_and_mismatched_receipt_stay_unknown(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    monkeypatch.setattr(tools, "_post", lambda *a, **k: challenge_response())
    run_reply(scheduled, tools)
    receipt = actions.list_actions()[0]
    def mismatch(url, **kwargs):
        sent.append(url)
        return Response({"success": True, "content_type": "post", "content_id": "other"})
    monkeypatch.setattr(tools, "_post", mismatch)
    for _ in range(2):
        agent, _ = agent_for([wants(call("moltbook_verify_reply", {"action_id": receipt["id"], "answer": "15.00"})), says("done")],
                             tools=[tools.moltbook_verify_reply])
        scheduler.run_task(scheduled, agent)
    assert len(sent) == 1
    assert actions.list_actions()[0]["state"] == "unknown"


def test_human_resolution_cannot_change_an_inflight_action(platform):
    from vegapunk import moltbook_actions as actions, task_history
    scheduled = task()
    actions.grant(scheduled.id, "account")
    run_id = task_history.begin(scheduled)
    action_id = actions.reserve(actions.Execution(scheduled.id, run_id), "account", "post", "parent", "hello")
    with pytest.raises(actions.ActionBlocked, match="ended runs"):
        actions.resolve(action_id, "rejected")


def test_resolution_checks_the_active_verification_run(platform):
    from vegapunk import moltbook_actions as actions, task_history
    scheduled = task()
    actions.grant(scheduled.id, "account")
    first = task_history.begin(scheduled)
    action_id = actions.reserve(actions.Execution(scheduled.id, first), "account", "post", "parent", "hello")
    actions.complete(action_id, "sending", "pending_verification", remote_id="remote",
                     code="code", challenge="20 minus 5", expires="2099-01-01T00:00:00.000000Z")
    task_history.finish(scheduled, first, "partial", "pending")
    second = task_history.begin(scheduled)
    actions.verification(actions.Execution(scheduled.id, second), action_id, "account")
    with pytest.raises(actions.ActionBlocked, match="ended runs"):
        actions.resolve(action_id, "rejected")


def test_grant_is_bound_to_the_authenticated_account(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "different-account")
    run_reply(scheduled, tools)
    assert sent == []


def test_profile_cannot_echo_credential_into_grant_or_ledger(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions
    tools, _ = platform
    scheduled = task()
    monkeypatch.setattr(tools, "_get", lambda *a, **k:
                        Response({"agent": {"id": "test-secret"}}))
    result = actions.command("grant", f"{scheduled.id} moltbook.reply_own")
    assert "test-secret" not in result
    assert "Could not grant" in result
    assert db.query("SELECT * FROM moltbook_permissions") == []


def test_secret_in_outgoing_content_is_blocked(platform):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    run_reply(scheduled, tools, content="do not send test-secret")
    assert sent == [] and actions.list_actions() == []


def test_v6_upgrade_keeps_history_and_adds_default_deny_tables():
    from vegapunk import moltbook_actions as actions
    scheduled = task()
    for table in ("moltbook_actions", "moltbook_permissions", "moltbook_backoff"):
        db.execute(f"DROP TABLE {table}")
    db.execute("UPDATE meta SET value='6' WHERE key='schema_version'")
    db.close_connection()
    assert scheduler.list_tasks()[0].id == scheduled.id
    assert actions.list_actions() == []
    assert db.query("SELECT * FROM moltbook_permissions") == []


def test_failed_intent_persistence_never_sends(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    def fail(*args, **kwargs):
        raise db.StoreError("intent persistence failed")
    monkeypatch.setattr(actions, "reserve", fail)
    run_reply(scheduled, tools)
    assert sent == [] and actions.list_actions() == []


def test_permission_context_explains_grant_without_broadening_authority(platform):
    from vegapunk import moltbook_actions as actions, prompt
    scheduled = task()
    assert "no write grant" in actions.context(scheduled.id)
    actions.grant(scheduled.id, "account")
    assert "moltbook.reply_own" in actions.context(scheduled.id)
    assert "Moltbook" in prompt.system_prompt(mode="unattended")


def test_create_and_verify_in_same_run_finishes_successfully(platform, monkeypatch):
    from vegapunk import moltbook_actions as actions, task_history
    tools, sent = platform
    scheduled = task()
    actions.grant(scheduled.id, "account")
    ids = iter(["a" * 32, "b" * 32])
    monkeypatch.setattr(db, "new_id", lambda: next(ids))
    def post(url, **kwargs):
        sent.append(url)
        if url.endswith("/verify"):
            return Response({"success": True, "content_type": "comment", "content_id": "remote-comment"})
        return challenge_response()
    monkeypatch.setattr(tools, "_post", post)
    agent, _ = agent_for([
        wants(call("moltbook_reply", {"post_id": "post", "parent_id": "parent", "content": "hello"})),
        wants(call("moltbook_verify_reply", {"action_id": "b" * 32, "answer": "15.00"})),
        says("API accepted the verified reply"),
    ], tools=[tools.moltbook_reply, tools.moltbook_verify_reply])
    scheduler.run_task(scheduled, agent)
    assert len(sent) == 2
    assert actions.list_actions()[0]["state"] == "accepted"
    assert task_history.list_runs()[0].status == "success"

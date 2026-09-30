"""Factual feedback is bounded and isolated, never a quality/engagement score."""

import json

import pytest

from tests.test_moltbook_drafts import draft_scope, draft, invoke
from tests.test_moltbook_publication import platform
from vegapunk import db, scheduler


def data(result):
    return json.loads(result.split("\n", 1)[1])


def test_profile_receives_own_draft_and_notebook_counts(draft_scope):
    task, _ = draft_scope
    draft(task)
    invoke(task, "moltbook_note", dict(kind="hypothesis", subject="Claim", text="Needs testing",
           source_id="source", quote="public evidence", confidence="low"))
    invoke(task, "moltbook_note", dict(kind="follow_up", subject="Recheck", text="Revisit claim",
           source_id="source", quote="public evidence", follow_up_on="2020-01-01"))
    result = invoke(task, "moltbook_insights", {})
    counts = data(result)
    assert counts["drafts"] == {"draft": 1}
    assert counts["notebook"]["kinds"] == {"follow_up": 1, "hypothesis": 1}
    assert counts["notebook"]["overdue_active"] == 1
    assert counts["runs"]["completed"] == 3
    assert counts["runs"]["running"] == 1
    assert "moltbook_test_secret" not in result and "Needs testing" not in result
    assert "model-assigned" in result and "engagement" in result


def test_insights_exclude_other_tasks_and_rotated_credentials(draft_scope):
    task, moltbook = draft_scope
    draft(task)
    invoke(task, "moltbook_note", dict(kind="observation", subject="Public finding", text="A useful observation",
           source_id="source", quote="public evidence"))
    scheduler.add_task("another", 300, profile="moltbook")
    other = scheduler.list_tasks()[1]
    other_counts = data(invoke(other, "moltbook_insights", {}))
    assert other_counts["drafts"] == {} and other_counts["sources"] == 0
    assert other_counts["notebook"]["kinds"] == {} and other_counts["tool_outcomes"] == {}
    moltbook.config.moltbook_credentials_file.write_text('{"api_key":"rotated"}')
    counts = data(invoke(task, "moltbook_insights", {}))
    assert counts["drafts"] == {} and counts["runs"] == {"running": 1}
    assert counts["sources"] == 0 and counts["notebook"]["statuses"] == {} and counts["tool_outcomes"] == {}


def test_insights_exclude_prior_profile_epoch(draft_scope):
    task, _ = draft_scope
    draft(task)
    invoke(task, "moltbook_note", dict(kind="hypothesis", subject="Public finding", text="An uncertain hypothesis",
           source_id="source", quote="public evidence"))
    scheduler.set_profile(task.id, "general")
    scheduler.set_profile(task.id, "moltbook")
    result = data(invoke(task, "moltbook_insights", {}))
    assert result["drafts"] == {} and result["runs"] == {"running": 1}
    assert result["sources"] == 0 and result["notebook"]["confidence"] == {} and result["tool_outcomes"] == {}


def test_general_interactive_and_missing_credentials_do_not_get_feedback(draft_scope):
    from vegapunk.tools import moltbook_insights
    task, moltbook = draft_scope
    assert moltbook_insights.moltbook_insights().startswith("Blocked:")
    scheduler.add_task("ordinary", 300)
    assert invoke(scheduler.list_tasks()[1], "moltbook_insights", {}).startswith("Blocked:")
    moltbook.config.moltbook_credentials_file.unlink()
    assert invoke(task, "moltbook_insights", {}).startswith("Blocked:")


def test_human_feedback_retains_deleted_task_history_without_loading_credentials(draft_scope, monkeypatch):
    from tests.fake_provider import session_for
    from tests.test_prompter import _complete
    from vegapunk.commands import CommandContext, dispatch
    task, moltbook = draft_scope
    draft(task)
    scheduler.remove_task(task.id)
    def forbidden(*args):
        raise AssertionError("human inspection/completion must be local-only")
    monkeypatch.setattr(moltbook, "_load_api_key", forbidden)
    ctx = CommandContext(session=session_for())
    result = dispatch(f"/schedule insights {task.id}", ctx).output
    counts = data(result)["tasks"][0]
    assert counts["task_id"] == task.id and counts["drafts"] == {"draft": 1}
    assert "insights" in _complete("/schedule ")
    assert task.id in _complete("/schedule insights ")
    assert "not found" in dispatch("/schedule insights nonexistent", ctx).output


def test_human_feedback_is_bounded_and_does_not_drop_audit_rows(draft_scope):
    from vegapunk import moltbook_insights
    for index in range(25):
        scheduler.add_task(f"task {index}", 300, profile="moltbook")
    before = db.query("SELECT COUNT(*) FROM scheduled_tasks")
    result = data(moltbook_insights.format_insights())
    assert len(result["tasks"]) == 20 and result["omitted_tasks"] is True
    assert len(json.dumps(result)) < 16000
    assert db.query("SELECT COUNT(*) FROM scheduled_tasks") == before


def test_cooldown_runs_are_counted_without_calling_provider(draft_scope):
    from tests.fake_provider import agent_for, says
    from vegapunk import moltbook_backoff
    from vegapunk import moltbook_insights
    task, _ = draft_scope
    moltbook_backoff.record("moltbook_test_secret", "600")
    agent, provider = agent_for(says("should not run"))
    assert "cooldown" in scheduler.run_task(task, agent)
    assert provider.requests == []
    counts = data(moltbook_insights.format_insights(task.id))["tasks"][0]
    assert counts["runs"] == {"blocked": 1}


@pytest.mark.parametrize("missing_credentials", [False, True])
def test_human_inspection_discovers_deleted_tasks_with_only_unscoped_runs(draft_scope, missing_credentials):
    from tests.fake_provider import agent_for, says
    from tests.test_prompter import _complete
    from vegapunk import moltbook_insights, task_history
    _, moltbook = draft_scope
    scheduler.add_task("Historical task", 300, profile="moltbook")
    task = scheduler.list_tasks()[-1]
    if missing_credentials:
        moltbook.config.moltbook_credentials_file.unlink()
        scheduler.run_task(task, agent_for(says("nothing to do"))[0])
    else:
        run = task_history.begin(task)
        task_history.finish(task, run, "completed", "Legacy run with no credential provenance")
    assert db.query("SELECT run_id FROM moltbook_run_scopes WHERE task_id=?", (task.id,)) == []
    scheduler.remove_task(task.id)
    counts = data(moltbook_insights.format_insights(task.id))["tasks"][0]
    assert counts["task_id"] == task.id and counts["runs"] == {"completed": 1}
    assert task.id in _complete("/schedule insights ")


def test_model_feedback_does_not_adopt_unscoped_run_history(draft_scope):
    from vegapunk import task_history
    task, _ = draft_scope
    run = task_history.begin(task)
    db.execute("INSERT INTO scheduled_run_events VALUES (?,?,?,?,?)",
               (run, 0, "moltbook_home", "success", db.utcnow()))
    task_history.finish(task, run, "success", "Legacy evidence without credential attribution")
    counts = data(invoke(task, "moltbook_insights", {}))
    assert counts["runs"] == {"running": 1} and counts["tool_outcomes"] == {}


def test_feedback_counts_self_review_without_certifying_quality(draft_scope):
    task, _ = draft_scope
    result = draft(task)
    draft_id = data(result)["draft_id"]
    invoke(task, "moltbook_review_draft", dict(draft_id=draft_id, verdict="ready", rationale="Later-run source review"))
    counts = data(invoke(task, "moltbook_insights", {}))
    assert counts["self_reviewed_drafts"] == 1 and counts["drafts"] == {"ready": 1}


def test_action_feedback_is_scoped_and_omits_raw_receipts(draft_scope):
    import hashlib
    task, _ = draft_scope
    stamp = db.utcnow()
    tag = hashlib.sha256(b"moltbook_test_secret").hexdigest()
    for action_id, state, credential, epoch in (
        ("accepted", "accepted", tag, task.profile_since),
        ("uncertain", "unknown", tag, task.profile_since),
        ("confirmed", "read_back_confirmed", tag, task.profile_since),
        ("old-key", "pending_verification", "prior-key", task.profile_since),
        ("old-epoch", "rejected", tag, "prior-epoch"),
        ("legacy", "unknown", "", ""),
    ):
        db.execute("INSERT INTO moltbook_actions (id,task_id,run_id,account_id,last_run_id,tool_call_id,post_id,parent_id,"
                   "content,content_hash,state,created_at,updated_at,credential_tag,profile_since,verification_code) "
                   "VALUES (?,?,?,'account',?,'call','post',?,'PRIVATE RECEIPT BODY','hash',?,?,?,?,?,'private-code')",
                   (action_id, task.id, "run", "run", action_id, state, stamp, stamp, credential, epoch))
    result = invoke(task, "moltbook_insights", {})
    counts = data(result)
    assert counts["actions"]["states"] == {"accepted": 1, "unknown": 1, "read_back_confirmed": 1}
    assert counts["actions"]["kinds"] == {"reply": 3}
    assert counts["actions"]["unresolved"] == 1
    assert "PRIVATE RECEIPT BODY" not in result and "private-code" not in result and "account" not in result
    from vegapunk import moltbook_insights
    human = data(moltbook_insights.format_insights(task.id))["tasks"][0]
    assert human["actions"]["kinds"] == {"reply": 6} and human["actions"]["unresolved"] == 3


def test_post_publishing_is_reflected_as_operational_receipts(platform):
    from tests.test_moltbook_publication import enable, create_draft, publish_run
    enable(platform)
    publish_run(platform, create_draft(platform))
    counts = data(invoke(platform["task"], "moltbook_insights", {}))
    assert counts["actions"]["states"] == {"accepted": 1} and counts["actions"]["kinds"] == {"post": 1}
    assert counts["actions"]["unresolved"] == 0 and counts["drafts"] == {"published": 1}
    assert counts["self_reviewed_drafts"] == 1 and counts["tool_outcomes"]["success"] == 3

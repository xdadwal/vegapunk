"""Plans use revisions and validated checkpoints, not model text as state."""

import pytest

from vegapunk import progress, session_store


def test_independent_steps_and_retry_safe_mutation():
    session_store.save_session("work", [])
    sid = session_store.session_identity("work")
    plan = progress.create(sid, "parallel", ["inspect", "research"], dependencies=[[], []])
    first = progress.update(sid, plan["id"], 0, "in_progress", 1, operation_id="start")
    assert progress.update(sid, plan["id"], 0, "in_progress", 1, operation_id="start") == first
    progress.update(sid, plan["id"], 1, "in_progress", 2)
    assert all(s["status"] == "in_progress" for s in progress.get(sid)["steps"])


def test_revision_rejects_cycle_and_protects_completed_descendants():
    session_store.save_session("work", [])
    sid = session_store.session_identity("work")
    with pytest.raises(ValueError, match="cycle"):
        progress.create(sid, "bad", ["one", "two"], dependencies=[[1], [0]])
    plan = progress.create(sid, "repair", ["inspect", "fix"])
    progress.update(sid, plan["id"], 0, "in_progress", 1)
    progress.update(sid, plan["id"], 0, "completed", 2, "observed", ["message:1"])
    progress.update(sid, plan["id"], 1, "in_progress", 3)
    with pytest.raises(ValueError, match="descendant"):
        progress.revise(sid, plan["id"], 4, "recheck", reopen=[0])


def test_dependency_and_revision_checks_are_atomic():
    session_store.save_session("work", [])
    sid = session_store.session_identity("work")
    plan = progress.create(sid, "repair", ["inspect", "fix"])
    with pytest.raises(ValueError, match="dependencies"):
        progress.update(sid, plan["id"], 1, "in_progress", 1)
    assert progress.get(sid)["revision"] == 1
    progress.update(sid, plan["id"], 0, "in_progress", 1)
    with pytest.raises(ValueError, match="revision"):
        progress.update(sid, plan["id"], 0, "completed", 1, "verified", ["event:1"])


def test_checkpoints_follow_identity_and_require_completion_evidence():
    session_store.save_session("work", [])
    sid = session_store.session_identity("work")
    plan = progress.create(sid, "inspect", ["check"])
    progress.update(sid, plan["id"], 0, "in_progress", 1)
    with pytest.raises(ValueError, match="evidence"):
        progress.update(sid, plan["id"], 0, "completed", 2)
    session_store.rename_session("work", "renamed", [])
    assert progress.get(session_store.session_identity("renamed"))["revision"] == 2
    assert len(progress.checkpoints(sid)) == 2


def test_bound_tools_keep_sessions_isolated():
    import json
    from vegapunk.tools import ALL_TOOLS
    from vegapunk.tools.progress import bind

    for name in ("one", "two"):
        session_store.save_session(name, [])
    sid = session_store.session_identity("one")
    tools = {t.name: t for t in bind(ALL_TOOLS, lambda: sid)}
    created = json.loads(tools["create_plan"](title="inspect", steps=["look"]))
    assert created["revision"] == 1
    assert progress.get(session_store.session_identity("two")) is None
    tools["update_plan_step"](plan_id=created["id"], step=0, status="in_progress", expected_revision=1)
    assert json.loads(tools["get_plan"]())["revision"] == 2


def test_session_reset_and_restore_clear_progress_identity():
    from tests.fake_provider import session_for, says

    session = session_for([says("done")])
    try:
        session.session_id = "old"
        session.reset()
        assert session.session_id is None
        session.session_id = "other"
        session.restore([])
        assert session.session_id is None
    finally:
        session.close()


def test_assigned_delegate_cannot_update_sibling_or_revise_plan():
    from vegapunk.tools import ALL_TOOLS
    from vegapunk.tools.progress import bind

    session_store.save_session("work", [])
    sid = session_store.session_identity("work")
    plan = progress.create(sid, "parallel", ["one", "two"], [[], []])
    progress.update(sid, plan["id"], 0, "in_progress", 1)
    tools = {t.name: t for t in bind(ALL_TOOLS, lambda: sid, plan["id"], 0)}
    with pytest.raises(ValueError, match="assigned"):
        tools["update_plan_step"](plan_id=plan["id"], step=1, status="in_progress", expected_revision=2)
    with pytest.raises(ValueError, match="primary"):
        tools["revise_plan"](plan_id=plan["id"], expected_revision=2, reason="change")
    assert progress.get(sid)["revision"] == 2


def test_claims_reject_duplicate_work_and_release_on_finish():
    session_store.save_session("work", [])
    sid = session_store.session_identity("work")
    plan = progress.create(sid, "inspect", ["one"], operation_id="create")
    assert progress.create(sid, "inspect", ["one"], operation_id="create") == plan
    progress.update(sid, plan["id"], 0, "in_progress", 1)
    progress.claim(sid, plan["id"], 0, "child-1")
    with pytest.raises(ValueError, match="active delegate"):
        progress.claim(sid, plan["id"], 0, "child-2")
    progress.release(plan["id"], 0, "child-1", "cancelled")
    progress.claim(sid, plan["id"], 0, "child-2")


def test_revision_retry_returns_original_snapshot_and_rejects_changed_payload():
    session_store.save_session("work", [])
    sid = session_store.session_identity("work")
    plan = progress.create(sid, "inspect", ["one"])
    changed = progress.revise(sid, plan["id"], 1, "expand", add_steps=["two"], operation_id="expand")
    assert progress.revise(sid, plan["id"], 1, "expand", add_steps=["two"], operation_id="expand") == changed
    with pytest.raises(ValueError, match="different arguments"):
        progress.revise(sid, plan["id"], 1, "different", operation_id="expand")


def test_concurrent_claims_have_one_owner():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from vegapunk import db

    session_store.save_session("work", [])
    sid = session_store.session_identity("work")
    plan = progress.create(sid, "inspect", ["one"])
    progress.update(sid, plan["id"], 0, "in_progress", 1)
    ready = Barrier(2)

    def attempt(owner):
        ready.wait()
        try:
            progress.claim(sid, plan["id"], 0, owner)
            return True
        except ValueError:
            return False
        finally:
            db.close_connection()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["one", "two"]))
    assert sorted(results) == [False, True]

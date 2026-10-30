"""Plans use revisions and validated checkpoints, not model text as state."""

import pytest

from vegapunk import progress, session_store


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

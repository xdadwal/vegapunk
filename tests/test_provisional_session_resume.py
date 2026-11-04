"""Interrupted first turns remain addressable using their displayed session name."""

from tests.fake_provider import session_for, user_turn, Turn
from vegapunk import cli, db, execution, session_store
from vegapunk.commands import CommandContext, dispatch
from vegapunk.prompter import ScriptedPrompter


def test_existing_overlong_provisional_name_can_be_resumed_and_removed():
    name = "untitled-" + "a" * 32
    assert len(name) > len(session_store.slugify(name))
    session_store.save_session(name, [user_turn("original")])
    canonical = session_store.slugify(name)
    session_store.save_session(canonical, [user_turn("different session")])
    sid = session_store.session_identity(name)
    session = session_for([])
    ctx = CommandContext(session=session)
    try:
        assert "Resumed" in dispatch(f"/sessions {name}", ctx).output
        assert ctx.current_name == name and session.session_id == sid
        assert session.messages == [user_turn("original")]
        assert session.backend.provider.call_count == 0
        assert "Removed" in dispatch(f"/sessions remove {name}", ctx).output
        assert not session_store.exists(name)
        assert session_store.load_session(canonical) == [user_turn("different session")]
    finally:
        session.close()


def test_failed_first_turn_creates_a_canonical_resumable_name():
    session = session_for([Turn(error=RuntimeError("synthetic provider failure"))])
    cli.main(prompter=ScriptedPrompter(["inspect", "/exit"]), session=session)
    name, sid = db.query("SELECT slug,session_id FROM sessions")[0]
    assert name.startswith("untitled-") and name == session_store.slugify(name)
    assert execution.list_runs(sid)[0].request == "inspect"
    resumed = session_for([])
    try:
        ctx = CommandContext(session=resumed)
        assert "Resumed" in dispatch(f"/sessions {name}", ctx).output
        assert resumed.session_id == sid
        assert resumed.backend.provider.call_count == 0
    finally:
        resumed.close()

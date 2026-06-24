"""Tests for the slash-command system and the session commands.

Commands run against a real Session (with a no-op FakeBrain — they never call the
model) and a tmp sessions dir.
"""

from __future__ import annotations

import pytest
from test_session import FakeBrain  # sibling module (tests/ is on sys.path)

from vegapunk.commands import CommandContext, dispatch
from vegapunk.session import Session


@pytest.fixture(autouse=True)
def _tmp_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr("vegapunk.session_store.sessions_dir", lambda: tmp_path)


def _ctx() -> CommandContext:
    return CommandContext(session=Session(FakeBrain([]), tools=[], system_prompt="SYS"))


def test_dispatch_returns_none_for_plain_text():
    # Not a slash command -> the REPL should send it to the model.
    assert dispatch("hello there", _ctx()) is None


def test_help_lists_the_commands():
    out = dispatch("/help", _ctx()).output
    for name in ("/help", "/save", "/load", "/sessions", "/new", "/exit"):
        assert name in out


def test_unknown_command_points_to_help():
    res = dispatch("/frobnicate", _ctx())
    assert "Unknown command" in res.output
    assert res.exit is False


def test_exit_sets_exit_flag():
    assert dispatch("/exit", _ctx()).exit is True
    assert dispatch("/quit", _ctx()).exit is True  # alias


def test_new_clears_history_and_unnames():
    ctx = _ctx()
    ctx.current_name = "old"
    ctx.session.restore([{"role": "system", "content": "SYS"}, {"role": "user", "content": "x"}])

    res = dispatch("/new", ctx)

    assert ctx.current_name is None
    assert ctx.session.messages == [{"role": "system", "content": "SYS"}]
    assert "new conversation" in res.output


def test_save_slugifies_and_persists():
    ctx = _ctx()
    ctx.session.restore(
        [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
        ]
    )

    res = dispatch("/save My Demo", ctx)

    assert "my-demo" in res.output
    assert ctx.current_name == "my-demo"
    # And it's listed afterward.
    assert "my-demo" in dispatch("/sessions", ctx).output


def test_save_requires_a_name():
    assert "Usage" in dispatch("/save    ", _ctx()).output


def test_save_renames_dropping_the_old_file():
    ctx = _ctx()
    ctx.session.restore([{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}])
    dispatch("/save first", ctx)
    dispatch("/save second", ctx)

    listed = dispatch("/sessions", ctx).output
    assert "second" in listed
    assert "first" not in listed  # the old name was dropped (rename, not copy)


def test_save_refuses_to_clobber_a_different_session():
    ctx = _ctx()
    ctx.session.restore([{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}])
    dispatch("/save taken", ctx)

    other = _ctx()
    other.session.restore([{"role": "system", "content": "SYS"}, {"role": "user", "content": "yo"}])
    res = dispatch("/save taken", other)

    assert "already exists" in res.output


def test_load_resumes_and_reports_turns():
    ctx = _ctx()
    ctx.session.restore(
        [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "yo"},
        ]
    )
    dispatch("/save demo", ctx)

    fresh = _ctx()
    res = dispatch("/load demo", fresh)

    assert "Resumed 'demo' (1 turns)" in res.output
    assert fresh.current_name == "demo"
    assert any(m.get("content") == "hi" for m in fresh.session.messages)


def test_load_missing_lists_what_exists():
    res = dispatch("/load ghost", _ctx())
    assert "No session 'ghost'" in res.output


def _convo(n: int) -> list[dict]:
    """A conversation with n user/assistant turns (q0/a0 … q{n-1}/a{n-1})."""
    msgs: list[dict] = [{"role": "system", "content": "SYS"}]
    for i in range(n):
        msgs.append({"role": "user", "content": f"q{i}"})
        msgs.append({"role": "assistant", "content": f"a{i}"})
    return msgs


def test_history_shows_recent_turns_without_system():
    ctx = _ctx()
    ctx.session.restore(_convo(3))
    out = dispatch("/history", ctx).output
    assert "q0" in out and "a0" in out and "q2" in out and "a2" in out
    assert "SYS" not in out  # the system turn is not a conversation turn


def test_history_caps_to_five_by_default():
    ctx = _ctx()
    ctx.session.restore(_convo(8))  # q0..q7
    out = dispatch("/history", ctx).output
    assert "q7" in out and "q3" in out  # last 5 turns kept (q3..q7)
    assert "q2" not in out  # older turns dropped


def test_history_accepts_a_count():
    ctx = _ctx()
    ctx.session.restore(_convo(8))
    out = dispatch("/history 2", ctx).output
    assert "q7" in out and "q6" in out
    assert "q5" not in out


def test_history_empty_conversation():
    assert "(no conversation yet)" in dispatch("/history", _ctx()).output


def test_history_rejects_a_non_numeric_count():
    assert "Usage" in dispatch("/history nope", _ctx()).output


def test_history_marks_unanswered_trailing_user():
    ctx = _ctx()
    ctx.session.restore(
        [{"role": "system", "content": "SYS"}, {"role": "user", "content": "still thinking?"}]
    )
    out = dispatch("/history", ctx).output
    assert "still thinking?" in out
    assert "vega: …" in out  # no reply yet -> placeholder


def test_history_count_larger_than_turns_shows_all():
    ctx = _ctx()
    ctx.session.restore(_convo(2))
    out = dispatch("/history 50", ctx).output
    assert "q0" in out and "q1" in out  # both turns, no slice error


def test_history_skips_tool_noise():
    ctx = _ctx()
    ctx.session.restore(
        [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "do it"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "RESULT"},
            {"role": "assistant", "content": "done"},
        ]
    )
    out = dispatch("/history", ctx).output
    assert "do it" in out and "done" in out  # paired the user msg with its text reply
    assert "RESULT" not in out  # the tool turn is not shown


def test_completer_offers_slash_commands_not_bare_keywords():
    # The REPL completer is derived from the registry, so it advertises the real
    # slash commands and never the removed bare keywords.
    from vegapunk.prompter import _COMMANDS

    assert "/save" in _COMMANDS and "/exit" in _COMMANDS
    assert "exit" not in _COMMANDS and "reset" not in _COMMANDS

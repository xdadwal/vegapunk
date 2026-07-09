"""Tests for the slash-command system and the session commands.

Commands run against a real Session (with a no-op FakeBrain — they never call the
model) and a tmp sessions dir.
"""

from __future__ import annotations

import pytest
from test_session import FakeBrain  # sibling module (tests/ is on sys.path)

from vegapunk import session_store
from vegapunk.commands import CommandContext, dispatch
from vegapunk.session import Session


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


@pytest.fixture
def skills_home(tmp_path, monkeypatch):
    monkeypatch.setattr("vegapunk.skills.skills_dir", lambda: tmp_path)
    return tmp_path


def _write_skill(home, name, text):
    """A skill in the Agent Skills layout: <name>/SKILL.md."""
    (home / name).mkdir(parents=True, exist_ok=True)
    (home / name / "SKILL.md").write_text(text, encoding="utf-8")


def test_skills_lists_name_and_description(skills_home):
    _write_skill(skills_home, "commit-message","---\ndescription: Commit rules\n---\nbody")
    result = dispatch("/skills", _ctx())
    assert "commit-message — Commit rules" in result.output


def test_skills_empty_points_at_the_directory(skills_home):
    result = dispatch("/skills", _ctx())
    assert "no skills" in result.output
    assert str(skills_home) in result.output


def test_skill_stages_for_the_next_message(skills_home):
    _write_skill(skills_home, "commit-message","---\ndescription: d\n---\nThe rules.")
    ctx = _ctx()
    result = dispatch("/skill commit", ctx)  # forgiving partial match
    assert "will be included with your next message" in result.output
    assert ctx.pending_skill is not None
    name, body = ctx.pending_skill
    assert name == "commit-message"
    assert body == "The rules."


def test_skill_bare_shows_usage_and_names(skills_home):
    _write_skill(skills_home, "commit-message","body")
    ctx = _ctx()
    result = dispatch("/skill", ctx)
    assert "Usage: /skill" in result.output
    assert "commit-message" in result.output
    assert ctx.pending_skill is None


def test_skill_unknown_name_corrects(skills_home):
    _write_skill(skills_home, "commit-message","body")
    ctx = _ctx()
    result = dispatch("/skill deploy", ctx)
    assert "No skill matches 'deploy'" in result.output
    assert "commit-message" in result.output
    assert ctx.pending_skill is None


def test_new_clears_a_staged_skill(skills_home):
    _write_skill(skills_home, "commit-message","body")
    ctx = _ctx()
    dispatch("/skill commit-message", ctx)
    assert ctx.pending_skill is not None
    dispatch("/new", ctx)
    assert ctx.pending_skill is None


def test_help_lists_skill_commands(skills_home):
    out = dispatch("/help", _ctx()).output
    assert "/skills" in out and "/skill" in out


def test_load_clears_a_staged_skill(skills_home, tmp_path):
    # Staged state belongs to the conversation it was staged in — restoring a
    # different one must drop it, exactly like /new does.
    _write_skill(skills_home, "commit-message","body")
    session_store.save_session("other", [{"role": "system", "content": "SYS"}])
    ctx = _ctx()
    dispatch("/skill commit-message", ctx)
    assert ctx.pending_skill is not None
    dispatch("/load other", ctx)
    assert ctx.pending_skill is None


def test_skills_lists_survivors_when_a_file_is_degraded(skills_home, capsys):
    _write_skill(skills_home, "good", "---\ndescription: Fine\n---\nbody")
    _write_skill(skills_home, "empty", "")
    result = dispatch("/skills", _ctx())
    assert "good — Fine" in result.output
    assert "empty" not in result.output  # skipped (with a stderr note), not listed


def test_skill_staged_body_is_capped(skills_home, monkeypatch):
    from dataclasses import replace

    from vegapunk.config import config as real_config

    _write_skill(skills_home, "big", "x" * 500)
    monkeypatch.setattr("vegapunk.commands.config", replace(real_config, output_char_cap=50))
    ctx = _ctx()
    dispatch("/skill big", ctx)
    _, body = ctx.pending_skill
    assert body.endswith("...[truncated]")
    assert "x" * 51 not in body


class _StubBrain(FakeBrain):
    """A FakeBrain with a fixed identity, standing in for a real provider."""

    def __init__(self, label: str) -> None:
        super().__init__([])
        self._label = label

    @property
    def model_label(self) -> str:
        return self._label


def test_model_without_arg_shows_the_active_model_and_choices():
    out = dispatch("/model", _ctx()).output
    assert "Active: unknown-model" in out  # FakeBrain's default identity
    assert "local" in out
    assert "claude" in out


def test_model_switches_the_live_brain_and_keeps_history(monkeypatch):
    stub = _StubBrain("claude:test")
    monkeypatch.setattr("vegapunk.commands.create_brain", lambda provider, cfg: stub)
    ctx = _ctx()
    before = ctx.session.messages

    res = dispatch("/model claude", ctx)

    assert ctx.session.brain is stub
    assert "claude:test" in res.output
    assert ctx.session.messages == before  # the conversation survived the swap


def test_model_with_unknown_provider_prints_usage(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        "vegapunk.commands.create_brain", lambda provider, cfg: calls.append(provider)
    )
    ctx = _ctx()
    original = ctx.session.brain

    res = dispatch("/model martian", ctx)

    assert res.output == "Usage: /model [local|claude [model]]"
    assert ctx.session.brain is original  # nothing swapped
    assert calls == []  # rejected before construction


def test_model_claude_with_a_name_overrides_the_configured_model(monkeypatch):
    seen: dict = {}

    def _record(provider, cfg):
        seen["provider"], seen["cfg"] = provider, cfg
        return _StubBrain("claude:opus")

    monkeypatch.setattr("vegapunk.commands.create_brain", _record)
    res = dispatch("/model claude opus", _ctx())

    assert seen["provider"] == "claude"
    assert seen["cfg"].claude_model == "opus"
    assert "claude:opus" in res.output


def test_model_claude_without_a_name_keeps_the_configured_default(monkeypatch):
    from vegapunk.commands import config as commands_config

    seen: dict = {}

    def _record(provider, cfg):
        seen["cfg"] = cfg
        return _StubBrain("claude")

    monkeypatch.setattr("vegapunk.commands.create_brain", _record)
    dispatch("/model claude", _ctx())

    assert seen["cfg"] is commands_config  # untouched: VEGAPUNK_CLAUDE_MODEL still rules


def test_model_rejects_a_model_name_for_local_and_extra_tokens(monkeypatch):
    calls: list = []
    monkeypatch.setattr(
        "vegapunk.commands.create_brain", lambda provider, cfg: calls.append(provider)
    )
    ctx = _ctx()

    assert dispatch("/model local opus", ctx).output == "Usage: /model [local|claude [model]]"
    assert dispatch("/model claude opus high", ctx).output == "Usage: /model [local|claude [model]]"
    assert calls == []


def test_model_surfaces_construction_errors_verbatim(monkeypatch):
    def _explode(provider, cfg):
        raise ValueError("Unknown effort level 'turbo' — expected one of: low, medium, high, xhigh, max.")

    monkeypatch.setattr("vegapunk.commands.create_brain", _explode)
    res = dispatch("/model claude", _ctx())

    assert "Unknown effort level" in res.output  # a real error, not the usage line


class _EffortStub(_StubBrain):
    """A brain with the effort surface, standing in for ClaudeBrain."""

    def __init__(self, label: str, effort: str | None = None) -> None:
        super().__init__(label)
        self.effort = effort

    def set_effort(self, level: str) -> None:
        self.effort = level


def test_model_swap_carries_the_session_effort_choice(monkeypatch):
    replacement = _EffortStub("claude:opus")
    monkeypatch.setattr("vegapunk.commands.create_brain", lambda provider, cfg: replacement)
    ctx = CommandContext(
        session=Session(_EffortStub("claude", effort="xhigh"), tools=[], system_prompt="SYS")
    )

    dispatch("/model claude opus", ctx)

    assert replacement.effort == "xhigh"  # /effort survived the model switch


def _effort_ctx(effort: str | None = None) -> CommandContext:
    return CommandContext(
        session=Session(_EffortStub("claude", effort=effort), tools=[], system_prompt="SYS")
    )


def test_effort_on_the_local_brain_explains_it_is_unsupported():
    ctx = _ctx()  # plain FakeBrain — no set_effort
    for line in ("/effort", "/effort max"):
        assert dispatch(line, ctx).output == (
            "(the local model has no effort setting — /model claude first)"
        )


def test_effort_bare_shows_the_sdk_default_when_unset():
    assert dispatch("/effort", _effort_ctx()).output == "Effort: high (default)"


def test_effort_bare_shows_the_current_level():
    assert dispatch("/effort", _effort_ctx("xhigh")).output == "Effort: xhigh"


def test_effort_sets_the_level_case_insensitively():
    ctx = _effort_ctx()
    res = dispatch("/effort XHIGH", ctx)
    assert ctx.session.brain.effort == "xhigh"
    assert res.output == "(effort set to xhigh)"


def test_effort_rejects_unknown_levels_with_the_valid_list():
    class _Picky(_EffortStub):
        def set_effort(self, level: str) -> None:
            raise ValueError("Unknown effort level 'turbo' — expected one of: low, medium, high, xhigh, max.")

    ctx = CommandContext(session=Session(_Picky("claude"), tools=[], system_prompt="SYS"))
    out = dispatch("/effort turbo", ctx).output
    for level in ("low", "medium", "high", "xhigh", "max"):
        assert level in out


def test_help_lists_effort():
    assert "/effort" in dispatch("/help", _ctx()).output


def test_help_lists_model():
    assert "/model" in dispatch("/help", _ctx()).output

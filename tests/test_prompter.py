"""Tests for the prompt_toolkit-backed prompter — deterministic, no real TTY.

Two layers, because prompt_toolkit applies completion / ghost-text acceptance in
the *renderer* (which DummyOutput no-ops): line submission and the custom newline
key-bindings are driven through a pipe; the completer and auto-suggest are checked
at the object layer.
"""

from __future__ import annotations

import pytest
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from vegapunk.prompter import PromptToolkitPrompter, ScriptedPrompter


def _prompter(tmp_path, inp):
    return PromptToolkitPrompter(history=InMemoryHistory(), input=inp, output=DummyOutput())


def test_plain_line_submits(tmp_path):
    with create_pipe_input() as inp:
        inp.send_text("hello world\r")
        assert _prompter(tmp_path, inp).prompt() == "hello world"


def test_ctrl_j_inserts_newline(tmp_path):
    # "\n" is Ctrl-J, bound to insert a newline; "\r" is Enter, which submits.
    with create_pipe_input() as inp:
        inp.send_text("line1\nline2\r")
        assert _prompter(tmp_path, inp).prompt() == "line1\nline2"


def test_esc_enter_inserts_newline(tmp_path):
    # "\x1b\r" is Esc-Enter, bound to insert a newline.
    with create_pipe_input() as inp:
        inp.send_text("first\x1b\rmore\r")
        assert _prompter(tmp_path, inp).prompt() == "first\nmore"


def test_history_persists_to_db(tmp_path):
    # Drive a prompter with the default DbHistory, then prove a fresh DbHistory
    # reads the entry back from the (conftest-isolated) database.
    from vegapunk.db_history import DbHistory

    with create_pipe_input() as inp:
        inp.send_text("remember me\r")
        PromptToolkitPrompter(input=inp, output=DummyOutput()).prompt()
    assert "remember me" in list(DbHistory().load_history_strings())


def _complete(text: str) -> list[str]:
    """What the live prompter would offer for ``text``, cursor at the end."""
    completer = PromptToolkitPrompter(history=InMemoryHistory())._session.completer
    return [c.text for c in completer.get_completions(Document(text, len(text)), CompleteEvent())]


def test_completer_suggests_commands_and_leaves_prose_alone():
    assert _complete("/cl") == ["/clear"]
    # A sentence is what you're mostly typing; popping a menu into it would be
    # worse than offering nothing.
    assert _complete("tell me ex") == []


def test_completer_offers_backends_after_a_model_command():
    for line in ("/model ", "/models "):
        offered = _complete(line)
        assert "claude" in offered and "codex" in offered and "local" in offered


def test_completer_narrows_backends_by_what_is_typed():
    assert _complete("/model cla") == ["claude", "claude-code"]


def test_completer_offers_effort_levels():
    assert _complete("/effort ") == ["low", "medium", "high", "xhigh", "max"]


def test_completer_offers_saved_sessions_for_load_and_forget():
    from vegapunk.session_store import save_session

    save_session("alpha-chat", [])
    save_session("beta-chat", [])

    assert set(_complete("/load ")) == {"alpha-chat", "beta-chat"}
    assert _complete("/sessions forget al") == ["alpha-chat"]
    assert _complete("/sessions ") == ["forget"]  # the sub-command first


def test_completer_never_makes_a_network_call_for_model_ids(monkeypatch):
    """Completion runs on every keystroke, so it may only offer ids already
    fetched — blocking the line you are typing on an HTTP round trip would be
    far worse than offering nothing until /models has been run once."""
    def _boom(*args, **kwargs):
        raise AssertionError("completion must not fetch models")

    monkeypatch.setattr("vegapunk.backend.available_models", _boom)

    assert _complete("/model claude ") == []


def test_completer_offers_cached_model_ids_once_they_are_known(monkeypatch):
    monkeypatch.setattr("vegapunk.prompter.cached_models", lambda name: ["claude-opus-5", "x"])

    assert _complete("/model claude claude-") == ["claude-opus-5"]


def test_auto_suggest_from_history():
    history = InMemoryHistory()
    history.append_string("hello there world")
    suggestion = AutoSuggestFromHistory().get_suggestion(Buffer(history=history), Document("hel", 3))
    assert suggestion is not None and suggestion.text == "lo there world"


def test_scripted_prompter_yields_then_eof():
    p = ScriptedPrompter(["first", "second"])
    assert p.prompt() == "first"
    assert p.prompt() == "second"
    with pytest.raises(EOFError):
        p.prompt()


def test_scripted_prompter_raises_queued_exception():
    p = ScriptedPrompter([KeyboardInterrupt, "after"])
    with pytest.raises(KeyboardInterrupt):
        p.prompt()
    assert p.prompt() == "after"


def test_status_callable_is_wired_to_the_bottom_toolbar():
    status = lambda: " gemma · my-chat"  # noqa: E731 — mirrors the CLI's wiring
    prompter = PromptToolkitPrompter(history=InMemoryHistory(), status=status)
    assert prompter._session.bottom_toolbar is status  # re-evaluated per render


def test_prompt_message_is_plain_off_a_tty():
    # Under pytest stdout isn't a TTY and the suite pins color mode "auto",
    # so the constructor must pick the plain string, not style tuples.
    prompter = PromptToolkitPrompter(history=InMemoryHistory())
    assert prompter._session.message == "❯ "


def test_prompt_message_is_gold_when_color_forced(monkeypatch):
    from dataclasses import replace

    from vegapunk import style

    monkeypatch.setattr("vegapunk.style.config", replace(style.config, color="always"))
    prompter = PromptToolkitPrompter(history=InMemoryHistory())
    assert prompter._session.message == [("bold fg:ansiyellow", "❯ ")]

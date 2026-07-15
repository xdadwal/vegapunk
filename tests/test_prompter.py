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
from prompt_toolkit.completion import CompleteEvent, WordCompleter
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


def test_prompter_uses_whole_line_command_completer():
    completer = PromptToolkitPrompter(history=InMemoryHistory())._session.completer
    assert isinstance(completer, WordCompleter)
    # Slash-command prefix → suggests the command; a normal sentence → stays silent.
    assert [c.text for c in completer.get_completions(Document("/cl", 3), CompleteEvent())] == ["/clear"]
    assert list(completer.get_completions(Document("tell me ex", 10), CompleteEvent())) == []


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
    assert prompter._session.message == "you> "


def test_prompt_message_is_gold_when_color_forced(monkeypatch):
    from dataclasses import replace

    from vegapunk import style

    monkeypatch.setattr("vegapunk.style.config", replace(style.config, color="always"))
    prompter = PromptToolkitPrompter(history=InMemoryHistory())
    assert prompter._session.message == [("bold fg:ansiyellow", "you> ")]

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
    return PromptToolkitPrompter(history_path=tmp_path / "history", input=inp, output=DummyOutput())


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


def test_history_persists_to_file(tmp_path):
    history_path = tmp_path / "history"
    with create_pipe_input() as inp:
        inp.send_text("remember me\r")
        PromptToolkitPrompter(history_path=history_path, input=inp, output=DummyOutput()).prompt()
    assert "remember me" in history_path.read_text()


def test_prompter_uses_whole_line_command_completer(tmp_path):
    completer = PromptToolkitPrompter(history_path=tmp_path / "history")._session.completer
    assert isinstance(completer, WordCompleter)
    # Whole-line prefix → suggests the command; mid-sentence → stays silent.
    assert [c.text for c in completer.get_completions(Document("cl", 2), CompleteEvent())] == ["clear"]
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

"""Tests for the REPL control flow — deterministic, no model/network/TTY.

We drive ``cli.main`` with a ``ScriptedPrompter`` (canned inputs) and a
``FakeBrain``-backed ``Session``, capturing stdout via capsys. Commands are
slash-prefixed (``/exit``, ``/new``, …) — there are no bare keyword commands.
A reply now auto-saves the conversation, so the sessions dir is redirected to a
tmp path and reply turns queue a second response for the auto-naming title call.
"""

from __future__ import annotations

import pytest
from test_session import FakeBrain, _text  # sibling test module (tests/ is on sys.path)

from vegapunk.cli import main
from vegapunk.prompter import ScriptedPrompter
from vegapunk.session import Session


@pytest.fixture(autouse=True)
def _tmp_sessions(tmp_path, monkeypatch):
    # Keep auto-save off the real repo and deterministic.
    monkeypatch.setattr("vegapunk.session_store.sessions_dir", lambda: tmp_path)


def _session(responses):
    return Session(FakeBrain(responses), tools=[], system_prompt="SYS")


def test_exit_quits(capsys):
    main(prompter=ScriptedPrompter(["/exit"]), session=_session([]))
    assert "bye." in capsys.readouterr().out


def test_quit_quits(capsys):
    main(prompter=ScriptedPrompter(["/quit"]), session=_session([]))
    assert "bye." in capsys.readouterr().out


def test_eof_prints_bye_and_returns(capsys):
    main(prompter=ScriptedPrompter([EOFError]), session=_session([]))
    assert "bye." in capsys.readouterr().out


def test_ctrl_c_at_prompt_continues_then_exits(capsys):
    main(prompter=ScriptedPrompter([KeyboardInterrupt, "/exit"]), session=_session([]))
    out = capsys.readouterr().out
    assert "interrupted — type /exit" in out  # proves the loop continued, didn't crash
    assert "bye." in out


def test_unknown_command_is_reported(capsys):
    main(prompter=ScriptedPrompter(["/nope", "/exit"]), session=_session([]))
    assert "Unknown command /nope" in capsys.readouterr().out


def test_empty_input_never_reaches_send(capsys):
    # FakeBrain has no queued responses, so if the empty turn called send()
    # the loop would blow up popping an empty list. Reaching "/exit" proves
    # empty input was skipped.
    main(prompter=ScriptedPrompter(["", "/exit"]), session=_session([]))
    assert "bye." in capsys.readouterr().out


def test_reply_is_printed_and_session_autosaved(capsys):
    # Queue: the turn reply, then the title for auto-naming.
    main(prompter=ScriptedPrompter(["hi", "/exit"]), session=_session([_text("yo"), _text("a chat")]))
    out = capsys.readouterr().out
    assert "vega> yo" in out
    assert "(saved as 'a-chat')" in out  # auto-named from the title call


def test_new_clears_history(capsys):
    session = _session([_text("hi there"), _text("greeting")])  # reply + title
    main(prompter=ScriptedPrompter(["hello", "/new", "/exit"]), session=session)
    out = capsys.readouterr().out
    assert "(new conversation)" in out
    assert session.messages == [{"role": "system", "content": "SYS"}]  # back to just the prompt


class _BoomSession:
    """A session whose send() always interrupts — to exercise the mid-turn path."""

    def send(self, _user_input: str) -> str:
        raise KeyboardInterrupt


def test_ctrl_c_during_send_is_caught(capsys):
    main(prompter=ScriptedPrompter(["go", "/exit"]), session=_BoomSession())
    out = capsys.readouterr().out
    assert "(interrupted)" in out  # caught the mid-generation interrupt
    assert "bye." in out  # and the loop survived to handle the next input


def test_autosave_failure_does_not_crash_repl(capsys, monkeypatch):
    # A disk error during the trailing autosave must degrade to a note, not tear
    # down the live conversation.
    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("vegapunk.session_store.save_session", boom)
    main(prompter=ScriptedPrompter(["hi", "/exit"]), session=_session([_text("yo"), _text("title")]))

    captured = capsys.readouterr()
    assert "vega> yo" in captured.out  # reply still shown
    assert "could not save" in captured.err  # degraded on stderr
    assert "bye." in captured.out  # survived to the next prompt and /exit

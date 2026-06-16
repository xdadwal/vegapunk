"""Tests for the REPL control flow — deterministic, no model/network/TTY.

We drive ``cli.main`` with a ``ScriptedPrompter`` (canned inputs) and a
``FakeBrain``-backed ``Session``, capturing stdout via capsys. This pins the
loop's branches: exit/quit, reset, empty input, the reply print, and the two
interrupt/EOF escape hatches.
"""

from __future__ import annotations

from test_session import FakeBrain, _text  # sibling test module (pytest puts tests/ on sys.path)

from vegapunk.cli import main
from vegapunk.prompter import ScriptedPrompter
from vegapunk.session import Session


def _session(responses):
    return Session(FakeBrain(responses), tools=[], system_prompt="SYS")


def test_exit_quits(capsys):
    main(prompter=ScriptedPrompter(["exit"]), session=_session([]))
    assert "bye." in capsys.readouterr().out


def test_quit_quits(capsys):
    main(prompter=ScriptedPrompter(["quit"]), session=_session([]))
    assert "bye." in capsys.readouterr().out


def test_eof_prints_bye_and_returns(capsys):
    main(prompter=ScriptedPrompter([EOFError]), session=_session([]))
    assert "bye." in capsys.readouterr().out


def test_ctrl_c_at_prompt_continues_then_exits(capsys):
    main(prompter=ScriptedPrompter([KeyboardInterrupt, "exit"]), session=_session([]))
    out = capsys.readouterr().out
    assert "interrupted — type 'exit'" in out  # proves the loop continued, didn't crash
    assert "bye." in out


def test_empty_input_never_reaches_send(capsys):
    # FakeBrain has no queued responses, so if the empty turn called send()
    # the loop would blow up popping an empty list. Reaching "exit" proves
    # empty input was skipped.
    main(prompter=ScriptedPrompter(["", "exit"]), session=_session([]))
    assert "bye." in capsys.readouterr().out


def test_reply_is_printed(capsys):
    main(prompter=ScriptedPrompter(["hi", "exit"]), session=_session([_text("yo")]))
    assert "vega> yo" in capsys.readouterr().out


def test_reset_clears_history(capsys):
    session = _session([_text("hi there")])
    main(prompter=ScriptedPrompter(["hello", "reset", "exit"]), session=session)
    out = capsys.readouterr().out
    assert "(history cleared)" in out
    assert session.messages == [{"role": "system", "content": "SYS"}]  # back to just the prompt


class _BoomSession:
    """A session whose send() always interrupts — to exercise the mid-turn path."""

    def send(self, _user_input: str) -> str:
        raise KeyboardInterrupt


def test_ctrl_c_during_send_is_caught(capsys):
    main(prompter=ScriptedPrompter(["go", "exit"]), session=_BoomSession())
    out = capsys.readouterr().out
    assert "(interrupted)" in out  # caught the mid-generation interrupt
    assert "bye." in out  # and the loop survived to handle the next input

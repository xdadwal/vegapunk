"""Tests for the approval gate — deterministic, no model/network/stdin.

Two halves. The gate itself (``vegapunk.gate``) is what logpose consults before
running each tool the model asked for; it's a plain callable, so it's driven
directly here for precise control over what's guarded and what the human said.
The ``CLIApprover``'s arrow-key menu is exercised by feeding keystrokes through
a prompt_toolkit pipe (a faked stdin gates the TTY check).

What the gate must never do is fail open: a guarded tool with nobody to ask is
blocked, not run.
"""

from __future__ import annotations

import asyncio

import pytest
from logpose import ToolGateResult, ToolUseBlock
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from vegapunk.approval import ApprovalPolicy, CLIApprover, Decision, ScriptedApprover
from vegapunk.gate import DENIED, NO_GATE, make_gate
from vegapunk.tools.registry import GUARDED


@pytest.fixture(autouse=True)
def _guarded_names(monkeypatch):
    """Guard exactly ``act`` and ``shell`` for these tests, nothing else."""
    monkeypatch.setattr("vegapunk.gate.GUARDED", {"act", "shell"})


def _ask(approver, name: str, arguments: dict | None = None):
    """Put one call to the gate and return its verdict."""
    block = ToolUseBlock(id="c1", name=name, input=arguments or {})
    return asyncio.run(make_gate(approver)(block))


def test_an_unguarded_tool_is_never_put_to_the_gate():
    # The approver would deny everything if asked — but it must never be asked.
    approver = ScriptedApprover(default=False)

    assert _ask(approver, "safe") is None  # None means "let it run"
    assert approver.calls == []


def test_a_guarded_tool_is_allowed_when_approved():
    approver = ScriptedApprover(default=True)

    assert _ask(approver, "act", {"x": 1}) is None
    assert approver.calls == [("act", {"x": 1})]  # asked, with the real arguments


def test_a_declined_tool_is_told_not_to_retry():
    assert _ask(ScriptedApprover(default=False), "act") == DENIED


def test_declining_with_feedback_steers_instead_of_denying():
    # The user's steer replaces the generic denial, and is reported as a
    # redirection rather than a failure — the model should act on it, not
    # treat it as something that broke.
    approver = ScriptedApprover(decisions={"shell": Decision(allow=False, feedback="use rg")})

    outcome = _ask(approver, "shell")

    assert isinstance(outcome, ToolGateResult)
    assert "use rg" in outcome.content
    assert outcome.content != DENIED
    assert outcome.is_error is False


def test_a_guarded_tool_without_an_approver_is_blocked():
    # Fail-closed: this is what makes an unattended scheduled run safe.
    assert _ask(None, "act") == NO_GATE


def test_an_unguarded_tool_still_runs_without_an_approver():
    # Read-only tools are the whole point of an unattended run.
    assert _ask(None, "safe") is None


def test_the_gate_reads_the_live_guarded_set():
    # GUARDED is populated by @tool(guarded=True) at import time; the gate must
    # consult it rather than a copy taken when it was built.
    assert {"write_file", "edit_file", "run_shell"} <= GUARDED


class _FakeStdin:
    def __init__(self, tty: bool) -> None:
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


# Arrow-key escape sequences + Enter, fed into the menu through the pipe.
DOWN = "\x1b[B"
UP = "\x1b[A"
ENTER = "\r"


class _PipeCLIApprover(CLIApprover):
    """Drives the real selection menu deterministically: one keystroke-string
    per expected ``approve()`` that reaches the menu, fed via a prompt_toolkit
    pipe. ``feedback_scripts`` does the same for the decline-with-feedback line
    prompt. If either is consulted more often than scripted, ``next`` raises."""

    def __init__(self, scripts: list[str], feedback_scripts: list[str] | None = None) -> None:
        super().__init__()
        self._scripts = iter(scripts)
        self._feedback_scripts = iter(feedback_scripts or [])

    def _ask(self, tool_name, arguments, *, input=None, output=None) -> str:
        with create_pipe_input() as inp:
            inp.send_text(next(self._scripts))
            return super()._ask(tool_name, arguments, input=inp, output=DummyOutput())

    def _ask_feedback(self, tool_name, arguments, *, input=None, output=None) -> str:
        with create_pipe_input() as inp:
            inp.send_text(next(self._feedback_scripts))
            return super()._ask_feedback(tool_name, arguments, input=inp, output=DummyOutput())


def test_cli_approver_yes_then_no(monkeypatch):
    monkeypatch.setattr("vegapunk.approval.sys.stdin", _FakeStdin(True))
    approver = _PipeCLIApprover([ENTER, DOWN + ENTER])  # default 'yes', then 'no'

    assert approver.approve("a", {}).allow is True
    assert approver.approve("b", {}).allow is False


def test_cli_approver_select_no(monkeypatch):
    monkeypatch.setattr("vegapunk.approval.sys.stdin", _FakeStdin(True))
    assert _PipeCLIApprover([DOWN + ENTER]).approve("act", {}).allow is False


def test_cli_approver_feedback_declines_with_message(monkeypatch):
    monkeypatch.setattr("vegapunk.approval.sys.stdin", _FakeStdin(True))
    # 3rd menu option ('feedback'), then a typed steer submitted with Enter.
    approver = _PipeCLIApprover([DOWN + DOWN + ENTER], feedback_scripts=["use rg instead" + ENTER])

    decision = approver.approve("run_shell", {})

    assert decision.allow is False
    assert decision.feedback == "use rg instead"


def test_cli_approver_feedback_empty_is_plain_decline(monkeypatch):
    monkeypatch.setattr("vegapunk.approval.sys.stdin", _FakeStdin(True))
    # Pick 'feedback' but type nothing: collapses to a plain decline (no steer).
    approver = _PipeCLIApprover([DOWN + DOWN + ENTER], feedback_scripts=[ENTER])

    decision = approver.approve("run_shell", {})

    assert decision.allow is False
    assert decision.feedback is None


def test_cli_approver_feedback_eof_is_plain_decline(monkeypatch):
    monkeypatch.setattr("vegapunk.approval.sys.stdin", _FakeStdin(True))
    # Ctrl-D (end-of-input) at the steer prompt == no steer -> plain decline.
    approver = _PipeCLIApprover([DOWN + DOWN + ENTER], feedback_scripts=["\x04"])

    decision = approver.approve("run_shell", {})

    assert decision.allow is False
    assert decision.feedback is None


def test_cli_approver_feedback_ctrl_c_cancels_the_turn(monkeypatch):
    monkeypatch.setattr("vegapunk.approval.sys.stdin", _FakeStdin(True))
    # Ctrl-C at the free-text steer prompt propagates (like the main REPL) so the
    # turn is cancelled by Session.send — it is NOT swallowed into a decline.
    approver = _PipeCLIApprover([DOWN + DOWN + ENTER], feedback_scripts=["\x03"])

    with pytest.raises(KeyboardInterrupt):
        approver.approve("run_shell", {})


def test_cli_approver_up_wraps_to_always(monkeypatch):
    monkeypatch.setattr("vegapunk.approval.sys.stdin", _FakeStdin(True))
    # Up from the top wraps to the last choice ('always').
    assert _PipeCLIApprover([UP + ENTER]).approve("act", {}).allow is True


def test_cli_approver_ctrl_c_in_menu_declines(monkeypatch):
    monkeypatch.setattr("vegapunk.approval.sys.stdin", _FakeStdin(True))
    assert _PipeCLIApprover(["\x03"]).approve("act", {}).allow is False  # Ctrl-C == decline


def test_cli_approver_remembers_always(monkeypatch):
    # Only ONE menu interaction is scripted. The second approve() of the same
    # tool must be served from memory — if it re-opened the menu, next() would
    # raise StopIteration and fail the test. A different tool still prompts.
    monkeypatch.setattr("vegapunk.approval.sys.stdin", _FakeStdin(True))
    # 'always' is now the 4th choice (index 3) after the feedback option.
    approver = _PipeCLIApprover([DOWN + DOWN + DOWN + ENTER, ENTER])  # 'always' for act, then 'yes' for other

    assert approver.approve("act", {}).allow is True  # selected 'always'
    assert approver.approve("act", {}).allow is True  # remembered — no menu
    assert approver.approve("other", {}).allow is True  # different tool — menu again ('yes')


def test_cli_approver_auto_denies_without_tty(monkeypatch):
    monkeypatch.setattr("vegapunk.approval.sys.stdin", _FakeStdin(False))

    def boom(*_a, **_k):
        raise AssertionError("the menu must not be shown when stdin is not a TTY")

    # An empty script: if _ask were reached, next() would raise — but the
    # non-TTY guard must short-circuit before the menu is ever built.
    approver = _PipeCLIApprover([])
    monkeypatch.setattr(approver, "_ask", boom)
    assert approver.approve("act", {}).allow is False


def test_auto_policy_bypasses_prompts_and_can_return_to_manual(monkeypatch):
    """The approver reads live policy state rather than its startup value."""
    monkeypatch.setattr("vegapunk.approval.sys.stdin", _FakeStdin(False))
    policy = ApprovalPolicy()
    approver = CLIApprover(policy)

    assert approver.approve("act", {}).allow is False
    assert policy.toggle() == "auto"
    assert approver.approve("act", {}).allow is True
    assert policy.toggle() == "manual"
    assert approver.approve("act", {}).allow is False

"""Tests for the approval gate — deterministic, no model/network/stdin.

The gate lives in ``loop._run_tool_batch``: guarded tools are approved in a
sequential pre-pass, then approved tools run (concurrently when there's more
than one) while denied ones short-circuit to a fixed message. We drive that
function directly for precise control over order and denial, and exercise the
``CLIApprover``'s arrow-key menu by feeding keystrokes through a prompt_toolkit
pipe (a faked stdin gates the TTY check).
"""

from __future__ import annotations

import pytest
from prompt_toolkit.input.defaults import create_pipe_input
from prompt_toolkit.output import DummyOutput

from vegapunk.approval import CLIApprover, Decision, ScriptedApprover
from vegapunk.brain import ToolCall
from vegapunk.loop import DENIED, NO_GATE, _run_tool_batch
from vegapunk.tools.base import Tool


def _tool(name: str, func, guarded: bool = False) -> Tool:
    return Tool(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}, "required": []},
        func=func,
        guarded=guarded,
    )


def _call(call_id: str, name: str, args: dict | None = None) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=args or {})


def test_unguarded_tool_runs_without_prompting():
    # Approver would deny everything if asked — but it must never be asked.
    approver = ScriptedApprover(default=False)
    results = _run_tool_batch({"safe": _tool("safe", lambda _a: "ran")}, [_call("c1", "safe")], approver)

    assert results[0][1] == "ran"
    assert approver.calls == []  # unguarded tool never consulted the gate


def test_guarded_tool_runs_when_approved():
    approver = ScriptedApprover(default=True)
    tool = _tool("act", lambda _a: "did it", guarded=True)

    results = _run_tool_batch({"act": tool}, [_call("c1", "act", {"x": 1})], approver)

    assert results[0][1] == "did it"
    assert approver.calls == [("act", {"x": 1})]  # asked, with the real args


def test_guarded_tool_denied_returns_message_and_does_not_run():
    ran: list[bool] = []

    def record(_a: dict) -> str:
        ran.append(True)
        return "should not happen"

    approver = ScriptedApprover(default=False)
    results = _run_tool_batch({"act": _tool("act", record, guarded=True)}, [_call("c1", "act")], approver)

    assert results[0][1] == DENIED
    assert ran == []  # the tool's function was never invoked


def test_guarded_tool_declined_with_feedback_steers_the_model():
    # Declining *with feedback* must feed the user's steer back as the result —
    # not the generic DENIED string — and still never run the tool.
    ran: list[bool] = []

    def record(_a: dict) -> str:
        ran.append(True)
        return "should not happen"

    approver = ScriptedApprover(decisions={"shell": Decision(allow=False, feedback="use rg")})
    results = _run_tool_batch(
        {"shell": _tool("shell", record, guarded=True)}, [_call("c1", "shell")], approver
    )

    assert ran == []
    assert "use rg" in results[0][1]
    assert results[0][1] != DENIED  # the steer replaced the generic denial


def test_mixed_batch_preserves_call_order_with_selective_denial():
    approver = ScriptedApprover(default=True, decisions={"write": False})
    by_name = {
        "write": _tool("write", lambda _a: "WROTE", guarded=True),  # guarded, denied
        "read": _tool("read", lambda _a: "READ"),  # unguarded, runs freely
        "shell": _tool("shell", lambda _a: "SHELL", guarded=True),  # guarded, approved
    }
    calls = [_call("c1", "write"), _call("c2", "read"), _call("c3", "shell")]

    results = _run_tool_batch(by_name, calls, approver)

    assert [c.id for c, _ in results] == ["c1", "c2", "c3"]  # order preserved
    assert [r for _, r in results] == [DENIED, "READ", "SHELL"]
    # Only the two guarded tools were ever put to the gate, in call order.
    assert [name for name, _ in approver.calls] == ["write", "shell"]


def test_guarded_tool_without_approver_is_blocked():
    # Fail-closed: a guarded tool with no approver wired must NOT run silently —
    # it's blocked with a distinct message, and its function is never invoked.
    ran: list[bool] = []

    def record(_a: dict) -> str:
        ran.append(True)
        return "should not happen"

    results = _run_tool_batch({"act": _tool("act", record, guarded=True)}, [_call("c1", "act")], approver=None)

    assert results[0][1] == NO_GATE
    assert ran == []


def test_unguarded_tool_runs_without_approver():
    # Read-only tools still run freely when no gate is wired (the FakeBrain
    # session tests rely on this).
    results = _run_tool_batch({"safe": _tool("safe", lambda _a: "ran")}, [_call("c1", "safe")], approver=None)
    assert results[0][1] == "ran"


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

"""Approval gates for guarded (side-effecting) tools.

Read-only tools run automatically; guarded tools must clear an ``Approver``
first. The loop runs approval as a sequential pre-pass (see ``loop._run_tool_batch``),
so an interactive approver never has to handle concurrent stdin prompts.

This mirrors ``brain.py``: a small ABC plus a real implementation for the CLI
and a deterministic fake for tests.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod

from prompt_toolkit.application import Application
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import Output

# The approval choices, in menu order: (returned value, label shown).
_CHOICES = [
    ("yes", "Yes (run it)"),
    ("no", "No (don't run)"),
    ("always", "Always allow this tool this session"),
]


def _build_menu(
    tool_name: str, arguments: dict, *, input: Input | None = None, output: Output | None = None
) -> Application:
    """An inline arrow-key approval menu: Up/Down to move, Enter to choose.

    Returns an Application whose ``.run()`` yields the chosen value
    ('yes' / 'no' / 'always'). Inline (not full-screen) and ``erase_when_done``
    so it disappears after choosing and the scrollback stays clean. ``input`` /
    ``output`` are passed only by tests (a prompt_toolkit pipe + DummyOutput).
    """
    state = {"idx": 0}

    def render():
        lines = [("bold", f"approve tool?  {tool_name}({arguments})\n")]
        for i, (_value, label) in enumerate(_CHOICES):
            selected = i == state["idx"]
            prefix = "> " if selected else "  "
            lines.append(("reverse" if selected else "", f"{prefix}{label}\n"))
        return lines

    kb = KeyBindings()

    @kb.add("up")
    def _(event) -> None:
        state["idx"] = (state["idx"] - 1) % len(_CHOICES)

    @kb.add("down")
    def _(event) -> None:
        state["idx"] = (state["idx"] + 1) % len(_CHOICES)

    @kb.add("enter")
    def _(event) -> None:
        event.app.exit(result=_CHOICES[state["idx"]][0])

    @kb.add("c-c")
    def _(event) -> None:
        event.app.exit(result="no")  # interrupting the menu == decline (safe)

    control = FormattedTextControl(render, focusable=True, show_cursor=False)
    body = HSplit([Window(control, height=len(_CHOICES) + 1)])
    return Application(
        layout=Layout(body),
        key_bindings=kb,
        full_screen=False,
        erase_when_done=True,
        input=input,
        output=output,
    )


class Approver(ABC):
    """Decides whether a guarded tool call may run."""

    @abstractmethod
    def approve(self, tool_name: str, arguments: dict) -> bool:
        ...


class CLIApprover(Approver):
    """Asks the user to approve each guarded tool via an arrow-key menu.

    "Always" is remembered for the life of this approver — which is one REPL
    session (the CLI builds one per run). A non-interactive stdin (pipe, CI,
    test harness) can't answer, so it auto-denies rather than blocking.
    """

    def __init__(self) -> None:
        self._always_allowed: set[str] = set()

    def approve(self, tool_name: str, arguments: dict) -> bool:
        if tool_name in self._always_allowed:
            return True

        # No interactive terminal: we cannot ask a human, so refuse (safe default).
        if not sys.stdin.isatty():
            print(
                f"  [approval] {tool_name}({arguments}) auto-denied (no interactive terminal).",
                file=sys.stderr,
            )
            return False

        choice = self._ask(tool_name, arguments)
        if choice == "always":
            self._always_allowed.add(tool_name)
            return True
        return choice == "yes"

    def _ask(
        self, tool_name: str, arguments: dict, *, input: Input | None = None, output: Output | None = None
    ) -> str:
        """Run the selection menu and return 'yes' | 'no' | 'always'.

        Isolated so tests can drive the real menu through a prompt_toolkit pipe.
        """
        return _build_menu(tool_name, arguments, input=input, output=output).run()


class ScriptedApprover(Approver):
    """Deterministic approver for tests.

    Answers ``default`` for any tool, unless ``decisions`` overrides it per
    name. Records every request in ``calls`` so tests can assert what was (and
    wasn't) asked.
    """

    def __init__(self, default: bool = True, decisions: dict[str, bool] | None = None) -> None:
        self._default = default
        self._decisions = dict(decisions or {})
        self.calls: list[tuple[str, dict]] = []

    def approve(self, tool_name: str, arguments: dict) -> bool:
        self.calls.append((tool_name, arguments))
        return self._decisions.get(tool_name, self._default)

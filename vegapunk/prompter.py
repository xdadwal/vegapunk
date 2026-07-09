"""Reading a line of user input — Vegapunk's REPL input layer.

Mirrors brain.py / approval.py: a small ABC, a real prompt_toolkit-backed
implementation for the CLI, and a deterministic fake for tests. The real
prompter gives persistent history (up/down recall across sessions), ghost-text
suggestions and command completion, deliberate multi-line composition
(Esc-Enter / Ctrl-J / paste), and prompt_toolkit's default emacs in-line editing.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from collections.abc import Callable

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import History
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import Output

from . import style
from .commands import REGISTRY as _COMMAND_REGISTRY
from .db_history import DbHistory

# The slash commands the REPL understands, offered as completions — derived from
# the command registry so they never drift from what the REPL actually handles.
_COMMANDS = sorted(f"/{name}" for name in _COMMAND_REGISTRY)


class Prompter(ABC):
    """Reads one message of user input.

    Contract matches builtins.input(): returns the submitted text (no trailing
    newline), raises EOFError on end-of-input (Ctrl-D) and KeyboardInterrupt on
    cancel (Ctrl-C) — so the CLI's existing handlers work unchanged.
    """

    @abstractmethod
    def prompt(self) -> str:
        ...


def _build_key_bindings() -> KeyBindings:
    kb = KeyBindings()

    # Insert a literal newline for deliberate multi-line composition; plain
    # Enter still submits (multiline=False). Ctrl-J (\n) works in every
    # terminal; Esc-Enter (\x1b\r) is what Option+Enter sends on macOS.
    #
    # Terminals can't distinguish Shift+Enter from Enter by default (both send
    # \r), so it can't be bound here directly. To get Shift+Enter, map it in
    # your terminal to send a newline — iTerm2: Settings > Profiles > Keys >
    # Key Mappings, add Shift+Enter -> "Send Hex Code" 0x0a — which this Ctrl-J
    # binding then turns into a newline.
    @kb.add(Keys.Escape, Keys.Enter)
    @kb.add(Keys.ControlJ)
    def _(event) -> None:
        event.current_buffer.insert_text("\n")

    return kb


class PromptToolkitPrompter(Prompter):
    """The real prompt: history, ghost text, command completion, multi-line."""

    def __init__(
        self,
        history: History | None = None,
        input: Input | None = None,
        output: Output | None = None,
        status: Callable[[], str] | None = None,
    ) -> None:
        if history is None:
            history = DbHistory()
        # Shaka gold for the person giving the orders — gated through the same
        # seam as everything else, so NO_COLOR/VEGAPUNK_COLOR strip it too.
        message = [("bold fg:ansiyellow", "you> ")] if style.enabled(sys.stdout) else "you> "
        self._session: PromptSession[str] = PromptSession(
            message=message,
            history=history,
            multiline=False,  # Enter submits; Up/Down recall history
            key_bindings=_build_key_bindings(),
            enable_history_search=False,  # plain chronological recall, not prefix-search
            auto_suggest=AutoSuggestFromHistory(),  # grey ghost text, accept with Right/End
            # sentence=True matches the whole line, so completion never pops up
            # mid-sentence — only when the line so far is a command prefix.
            completer=WordCompleter(_COMMANDS, sentence=True),
            complete_while_typing=True,
            # A callable is re-evaluated on every render, so a status line
            # like "model · session-name" stays current without any wiring.
            bottom_toolbar=status,
            input=input,
            output=output,
        )

    def prompt(self) -> str:
        # prompt_toolkit raises EOFError on Ctrl-D and KeyboardInterrupt on
        # Ctrl-C, exactly like builtins.input().
        return self._session.prompt()


class ScriptedPrompter(Prompter):
    """Deterministic prompter for tests — no TTY, no prompt_toolkit.

    Each queued item is either returned (a string) or raised (an exception type
    or instance), letting tests drive every CLI path: a normal turn, exit, an
    interrupt at the prompt, end-of-input. An exhausted queue raises EOFError.
    """

    def __init__(self, inputs: list[str | BaseException | type[BaseException]]) -> None:
        self._inputs = list(inputs)

    def prompt(self) -> str:
        if not self._inputs:
            raise EOFError
        item = self._inputs.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, type) and issubclass(item, BaseException):
            raise item
        return item

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
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import History
from prompt_toolkit.input import Input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import Output

from . import skills, style
from .backend import EFFORT_LEVELS, backend_names, cached_models
from .commands import REGISTRY as _COMMAND_REGISTRY
from .db_history import DbHistory
from .session_store import list_sessions

# The slash commands the REPL understands, offered as completions — derived from
# the command registry so they never drift from what the REPL actually handles.
_COMMANDS = sorted(f"/{name}" for name in _COMMAND_REGISTRY)

# Sub-commands that take a fixed vocabulary of their own.
_SUBCOMMANDS = {
    "sessions": ["remove"],
    "schedule": ["list", "add", "remove"],
}


def _session_names() -> list[str]:
    """Saved conversation names, newest first. Degrades to none on a DB error —
    a completer must never be the thing that breaks the prompt."""
    try:
        return [name for name, _turns, _updated in list_sessions()]
    except Exception:  # noqa: BLE001 — completion is a convenience
        return []


def _skill_names() -> list[str]:
    try:
        return [skill.name for skill in skills.list_skills()]
    except Exception:  # noqa: BLE001 — same
        return []


def _argument_options(command: str, words: list[str]) -> list[str]:
    """What can follow ``/command`` given the words already typed.

    ``words`` is everything after the command name, the last entry being the
    (possibly empty) word under the cursor.
    """
    position = len(words) - 1  # 0 = first argument, 1 = second, …
    if command == "model":
        # First a backend, then that backend's models — but only ones already
        # fetched, so a keystroke never waits on the network.
        return backend_names() if position == 0 else cached_models(words[0])
    if command == "effort":
        return list(EFFORT_LEVELS) if position == 0 else []
    if command == "save":
        return _session_names() if position == 0 else []
    if command == "skill":
        return _skill_names() if position == 0 else []
    if command == "sessions":
        if position == 0:
            return [*_SUBCOMMANDS["sessions"], *_session_names()]
        return _session_names() if words[0] == "remove" else []
    if command in _SUBCOMMANDS:
        return _SUBCOMMANDS[command] if position == 0 else []
    return []


class SlashCompleter(Completer):
    """Completes a slash command, then completes its arguments.

    Only ever fires on a line starting with ``/`` — ordinary prose is what you
    are mostly typing, and popping a menu into the middle of a sentence would be
    worse than no completion at all.
    """

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/") or "\n" in text:
            return
        head, *rest = text[1:].split(" ")
        if not rest:  # still typing the command name
            for name in _COMMANDS:
                if name.startswith(text):
                    yield Completion(name, start_position=-len(text))
            return
        word = rest[-1]
        for option in _argument_options(head.lower(), rest):
            if option.startswith(word):
                yield Completion(option, start_position=-len(word))


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
        message = [("bold fg:ansiyellow", "❯ ")] if style.enabled(sys.stdout) else "❯ "
        self._session: PromptSession[str] = PromptSession(
            message=message,
            history=history,
            multiline=False,  # Enter submits; Up/Down recall history
            key_bindings=_build_key_bindings(),
            enable_history_search=False,  # plain chronological recall, not prefix-search
            auto_suggest=AutoSuggestFromHistory(),  # grey ghost text, accept with Right/End
            # Completes the command name, then its arguments — providers,
            # models, session names, skills, effort levels. Never fires unless
            # the line starts with "/", so prose is left alone.
            completer=SlashCompleter(),
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

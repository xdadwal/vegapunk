"""Interactive question-and-answer support for agent tool calls.

The agent loop runs synchronous tools on a worker thread, so this module keeps
the terminal interaction behind a small interface. The CLI installs one
``CLIQuestioner`` while it owns the terminal; unattended runs leave it absent
and receive a factual unavailable result instead of blocking on stdin.
"""

from __future__ import annotations

import json
import sys
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

from prompt_toolkit import PromptSession
from prompt_toolkit.input import Input
from prompt_toolkit.output import Output

from . import menu

CUSTOM = "__custom__"
CANCELLED = (
    "User dismissed the question. Do not ask the same question again; proceed with "
    "available information or state what is still needed."
)
UNAVAILABLE = (
    "User input is unavailable in this run. Do not retry ask_user; use available "
    "information or state what is still needed."
)


@dataclass(frozen=True)
class _NotedOption:
    """A picker option submitted with text from its inline note field."""

    option: str
    note: str


class Questioner(ABC):
    """Obtains one answer to a structured agent question."""

    @abstractmethod
    def ask(self, question: str, options: list[str], preferred_option: str) -> str:
        """Ask one question and return a model-readable answer."""


class CLIQuestioner(Questioner):
    """Ask the terminal user through Vegapunk's shared picker.

    A lock prevents two model-requested questions from competing for the one
    terminal if a provider schedules tool calls concurrently.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()

    def ask(self, question: str, options: list[str], preferred_option: str) -> str:
        if not sys.stdin.isatty():
            return UNAVAILABLE
        with self._lock:
            choice = self._choose(question, options, preferred_option)
            if choice is None:
                return CANCELLED
            note: str | None = None
            if isinstance(choice, _NotedOption):
                note = choice.note or None
                choice = choice.option
            if choice == CUSTOM:
                answer = self._custom_answer()
                return _answered(answer, custom=True, note=note) if answer else CANCELLED
            return _answered(choice, note=note)

    def _choose(
        self,
        question: str,
        options: list[str],
        preferred_option: str,
        *,
        input: Input | None = None,
        output: Output | None = None,
    ) -> str | _NotedOption | None:
        """Run the option picker; isolated so tests can drive real key input."""
        title: menu.FormattedText = [
            ("bold", "Vegapunk needs your input\n"),
            ("", f"{question}\n"),
            (
                "class:dim",
                "Choose an option or type your own answer. Ctrl+N opens an inline note field.\n",
            ),
        ]
        choices = [
            menu.Option(
                value=option,
                label=option,
                active=option == preferred_option,
            )
            for option in options
        ]
        choices.append(menu.Option(value=CUSTOM, label="Other — type your own answer"))
        choice = menu.choose(
            title,
            choices,
            input=input,
            output=output,
            inline_editor=menu.InlineEditor(
                key="c-n",
                label="ctrl+n add note",
                prompt="note",
                on_submit=lambda option, note: _NotedOption(option, note),
            ),
        )
        if choice is None or isinstance(choice, _NotedOption):
            return choice
        if not isinstance(choice, str):
            raise TypeError("question picker returned an unexpected result")
        return choice

    def _custom_answer(
        self, *, input: Input | None = None, output: Output | None = None
    ) -> str:
        """Read a custom response after the user chooses the final menu row."""
        try:
            return PromptSession(
                message="your answer > ", input=input, output=output
            ).prompt().strip()
        except EOFError:
            return ""

class ScriptedQuestioner(Questioner):
    """Deterministic questioner for tests and non-terminal embeddings."""

    def __init__(self, answers: list[str | None], notes: list[str | None] | None = None) -> None:
        self._answers = list(answers)
        self._notes = list(notes or [])
        self.calls: list[tuple[str, list[str], str]] = []

    def ask(self, question: str, options: list[str], preferred_option: str) -> str:
        self.calls.append((question, options, preferred_option))
        if not self._answers:
            return UNAVAILABLE
        answer = self._answers.pop(0)
        if answer is None:
            return CANCELLED
        note = self._notes.pop(0) if self._notes else None
        return _answered(answer, custom=answer not in options, note=note)


_questioner: Questioner | None = None


def set_questioner(questioner: Questioner | None) -> None:
    """Install the interactive question handler for this process's live REPL."""
    global _questioner
    _questioner = questioner


def ask(question: str, options: list[str], preferred_option: str) -> str:
    """Route a validated tool call to the current interactive questioner."""
    if _questioner is None:
        return UNAVAILABLE
    return _questioner.ask(question, options, preferred_option)


def validate(
    question: str, options: list[str], preferred_option: str
) -> tuple[str, list[str], str]:
    """Normalise and validate the compact question contract exposed to models."""
    if not isinstance(question, str):
        raise ValueError("question must be a string")
    if not isinstance(options, list) or any(not isinstance(option, str) for option in options):
        raise ValueError("options must be a list of strings")
    if not isinstance(preferred_option, str):
        raise ValueError("preferred_option must be a string")
    normalized_question = " ".join(question.split())
    normalized_options = [" ".join(option.split()) for option in options]
    normalized_preferred = " ".join(preferred_option.split())
    if not normalized_question:
        raise ValueError("question must not be empty")
    if not 1 <= len(normalized_options) <= 5:
        raise ValueError("options must contain between 1 and 5 choices")
    if any(not option for option in normalized_options):
        raise ValueError("options must not contain empty choices")
    if len(set(normalized_options)) != len(normalized_options):
        raise ValueError("options must be distinct")
    if CUSTOM in normalized_options:
        raise ValueError("options must not use the reserved custom-answer value")
    if normalized_preferred not in normalized_options:
        raise ValueError("preferred_option must exactly match one of options")
    return normalized_question, normalized_options, normalized_preferred


def ask_from_tool(question: str, options: list[str], preferred_option: str) -> str:
    """Validate a model tool call and collect its answer from the live user."""
    question, options, preferred_option = validate(question, options, preferred_option)
    return ask(question, options, preferred_option)


def _answered(answer: str, *, custom: bool = False, note: str | None = None) -> str:
    """Return an unambiguous result the model can use in the next loop step."""
    kind = "custom response" if custom else "option"
    note_line = f"\nUser note: {json.dumps(note, ensure_ascii=False)}" if note else ""
    return (
        f"User selected {kind}: {json.dumps(answer, ensure_ascii=False)}{note_line}\n\n"
        "Interactive questioning protocol: if another preference or decision is needed, "
        "call ask_user again. Do not ask a question or present options in assistant text; "
        "when you have enough information, begin the completed response with FINAL:."
    )

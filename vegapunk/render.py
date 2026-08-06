"""What a turn looks like — the seam every printed byte goes through.

``loop.py`` decides *what* happened; this decides how it appears. Splitting them
is what lets the terminal UI change without touching the event handling, and
what lets a piped or scripted session keep the plain text it has always had.

Two channels, deliberately separate. Reply text goes to stdout so it can be
piped; everything else — steps, reasoning, tool results, warnings — goes to
stderr, so watching the loop work never contaminates the answer.
"""

from __future__ import annotations

import sys
from typing import Protocol, runtime_checkable

from . import style
from .config import Config, config


def _shorten(result: str, limit: int = 200) -> str:
    """Trim a tool result for display; the model always gets the full text.

    200 chars keeps a whole-file read from flooding the watch channel while
    still showing enough to recognise what came back. Slicing a ``str`` is
    codepoint-safe, so this never splits a character.
    """
    extra = len(result) - limit
    if extra <= 0:
        return result
    return f"{result[:limit]}… (+{extra:,} more char{'s' if extra != 1 else ''})"


@runtime_checkable
class Renderer(Protocol):
    """How a turn is shown. One instance spans both channels of one turn."""

    def step(self, number: int) -> None:
        """A model round trip began."""

    def reasoning(self, text: str) -> None:
        """A fragment of the model's chain of thought arrived."""

    def reasoning_end(self) -> None:
        """No more reasoning this turn. Idempotent — callers needn't check."""

    def tool_result(self, name: str, arguments: dict, content: str, is_error: bool) -> None:
        """A tool ran and produced ``content``."""

    def note(self, text: str) -> None:
        """A warning about the loop itself, not about the model's answer."""

    def reply_delta(self, text: str) -> None:
        """A fragment of the assistant's answer arrived."""

    def reply_end(self) -> None:
        """The answer is complete. Idempotent."""


class PlainRenderer:
    """Unstyled, line-at-a-time output — what a pipe, a log, or a test sees.

    Every method writes complete lines and never moves the cursor, so the
    output survives redirection, ``less``, and CI unchanged.
    """

    def __init__(self) -> None:
        self._reasoning_open = False
        self._reasoning_reset = ""
        self._spoke = False
        self._line_open = False

    # -- the watch channel (stderr) -----------------------------------------

    def step(self, number: int) -> None:
        # The marker shows where each model round trip starts, which is what
        # makes batched-vs-chained tool calling visible.
        print(style.paint(f"  [think] step {number}", style.DIM, sys.stderr), file=sys.stderr)

    def reasoning(self, text: str) -> None:
        if not self._reasoning_open:
            # Colour opens once at the line start and resets once at the close,
            # not per fragment: a Ctrl-C landing mid-reasoning would otherwise
            # stain the whole terminal dim.
            self._reasoning_reset = style.RESET if style.enabled(sys.stderr) else ""
            opening = style.DIM + style.MAGENTA if self._reasoning_reset else ""
            print(f"{opening}  [reason] ", end="", file=sys.stderr, flush=True)
            self._reasoning_open = True
        print(text, end="", file=sys.stderr, flush=True)

    def reasoning_end(self) -> None:
        if self._reasoning_open:
            self._reasoning_open = False
            print(self._reasoning_reset, file=sys.stderr)

    def tool_result(self, name: str, arguments: dict, content: str, is_error: bool) -> None:
        marker = style.paint(
            f"  [tool] {name}", style.RED if is_error else style.CYAN, sys.stderr
        )
        print(f"{marker}({arguments}) -> {_shorten(content)}", file=sys.stderr)

    def note(self, text: str) -> None:
        print(style.paint(f"  [note] {text}", style.YELLOW, sys.stderr), file=sys.stderr)

    # -- the answer channel (stdout) ----------------------------------------

    def reply_delta(self, text: str) -> None:
        if not text:
            return
        if not self._spoke:
            print(self._prefix(), end="", flush=True)
            self._spoke = True
        print(text, end="", flush=True)
        self._line_open = not text.endswith("\n")

    def reply_end(self) -> None:
        if not self._spoke:
            print(self._prefix())  # an empty reply still gets its prompt line
        elif self._line_open:
            print()
        self._spoke = False
        self._line_open = False

    @staticmethod
    def _prefix() -> str:
        """Punk Records speaking. The reset lands before the space so the reply
        itself streams in the default colour."""
        return style.paint("vega>", style.BOLD + style.MAGENTA, sys.stdout) + " "


UI_MODES = ("auto", "rich", "plain")


def pick(cfg: Config = config) -> Renderer:
    """The renderer for this process.

    ``auto`` follows the terminal, which is the same rule ``style.enabled``
    already applies to colour, so one mental model covers both. ``NO_COLOR``
    forces plain: a user asking for no escape codes is not asking for a live
    re-rendering region either.

    Raises:
        ValueError: If ``VEGAPUNK_UI`` is not a known mode. Said out loud rather
            than defaulting, because silently ignoring the setting is how you
            spend an afternoon wondering why it does nothing.
    """
    if cfg.ui not in UI_MODES:
        raise ValueError(
            f"Unknown VEGAPUNK_UI {cfg.ui!r} — expected one of: {', '.join(UI_MODES)}."
        )
    return PlainRenderer()

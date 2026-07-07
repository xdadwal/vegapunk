"""ANSI color for the CLI's two channels — stdout (replies) and stderr (trace).

Display-only sugar. Plain prints go through ``paint``, which returns the text
unchanged when the target stream shouldn't get color — so piped output, logs,
and tests see exactly the plain text they always did. prompt_toolkit widgets
(the input prompt, the approval menu) style themselves, but gate on the same
``enabled`` so one setting governs everything. The palette itself is themed
on Dr. Vegapunk: reasoning wears Punk Records magenta (the model's thoughts
*are* the externalized brain murmuring), tools glow Egghead cyan, failures go
Atlas red, and the out-of-tokens note is York yellow.
"""

from __future__ import annotations

import os
import sys
from typing import TextIO

from .config import config

# SGR escape codes; compose by concatenation (BOLD + MAGENTA). DIM is the
# classic "inner voice" rendering — if a terminal shows it poorly, bright
# black (\x1b[90m) is the drop-in swap.
RESET = "\x1b[0m"
BOLD = "\x1b[1m"
DIM = "\x1b[2m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
MAGENTA = "\x1b[35m"
CYAN = "\x1b[36m"


def enabled(stream: TextIO) -> bool:
    """Should ANSI codes be written to this stream?

    Checked per stream and at call time: ``python -m vegapunk 2>trace.log``
    keeps the stdout replies colored while the redirected trace stays plain.
    An explicit VEGAPUNK_COLOR=always beats NO_COLOR — the app-specific
    opt-in is more deliberate than the blanket standard.
    """
    if config.color == "always":
        return True
    if config.color == "never" or os.getenv("NO_COLOR"):
        return False
    return hasattr(stream, "isatty") and stream.isatty()


def paint(text: str, code: str, stream: TextIO) -> str:
    """Wrap ``text`` in ``code``+RESET when ``stream`` gets color; else return
    it unchanged (identical bytes, not merely similar — tests rely on this)."""
    return f"{code}{text}{RESET}" if enabled(stream) else text


def _demo() -> None:  # pragma: no cover — eyeball check: python -m vegapunk.style
    for name in ("BOLD", "DIM", "RED", "GREEN", "YELLOW", "MAGENTA", "CYAN"):
        print(paint(f"{name.lower():>8} sample", globals()[name], sys.stdout))


if __name__ == "__main__":
    _demo()

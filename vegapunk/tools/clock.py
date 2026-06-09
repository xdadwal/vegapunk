"""A tool that reports the current local date and time.

Note how little this takes now: a typed function + a docstring + @tool. That's
the whole point of the decorator — adding a capability is nearly free.
"""

from __future__ import annotations

from datetime import datetime

from .registry import tool


@tool
def get_time() -> str:
    """Return the current local date and time."""
    return datetime.now().strftime("%A, %d %B %Y, %H:%M:%S")

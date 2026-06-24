"""The ``remember`` tool — let Vegapunk save a durable fact for future sessions.

Unguarded: this writes to Vegapunk's own memory file under ``.vegapunk/``, not to
the user's workspace, so it doesn't go through the approval gate the way
file/shell tools do. The store itself lives in ``vegapunk/memory.py``; the saved
facts are auto-loaded into the system prompt at session start, so there is no
separate recall tool.
"""

from __future__ import annotations

from ..memory import save_memory
from .registry import tool


@tool
def remember(fact: str) -> str:
    """Save a durable fact or preference about the user so you still know it in
    future sessions. Call this when the user states a standing preference or fact
    about themselves (their tools, environment, how they like things done), or
    asks you to remember something. Don't save ephemeral, one-off task details."""
    return save_memory(fact)

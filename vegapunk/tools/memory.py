"""Memory tools — let Vegapunk save and look up durable facts.

Both are unguarded: they touch Vegapunk's own memory in the embedded database,
not the user's workspace, so they don't go through the approval gate the way
file/shell tools do. The store lives in ``vegapunk/memory.py``; saved facts are
also folded into the system prompt at session start, so ``recall`` is for
checking something specific that may not be visible there.
"""

from __future__ import annotations

from ..memory import recall_memory, save_memory
from .registry import tool


@tool
def remember(fact: str) -> str:
    """Save a durable fact or preference about the user so you still know it in
    future sessions. Call this when the user states a standing preference or fact
    about themselves (their tools, environment, how they like things done), or
    asks you to remember something. Don't save ephemeral, one-off task details."""
    return save_memory(fact)


@tool
def recall(query: str) -> str:
    """Search your long-term memory for saved facts related to ``query``. Your
    memory is also summarized in your system prompt; call this when you need to
    check for something specific that may not be visible there — a preference,
    fact, or detail from past sessions. Uses semantic similarity when available,
    plain text match otherwise."""
    hits = recall_memory(query)
    if not hits:
        return "No matching memories."
    return "\n".join(f"- [{m.created_at[:10]}] {m.content}" for m in hits)

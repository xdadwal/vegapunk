"""The one gate every tool call passes through."""

from collections.abc import Callable, Iterable
from typing import Any

NOT_AVAILABLE = "That tool isn't available."
BUDGET_USED = "You have used all the lookups allowed for one answer. Answer with what you have."


def make_gate(allowed: Iterable[str], max_calls: int) -> Callable[[Any], str | None]:
    """A gate for logpose's `on_tool_call`, with its own counter, so make one per answer.

    It refuses any tool that is not on the allowed list and every call after the
    `max_calls`-th. Returning None lets a call through; a string blocks it and is shown to
    the model as the tool's error.
    """
    names = frozenset(allowed)
    used = 0

    def gate(call: Any) -> str | None:
        nonlocal used
        if call.name not in names:
            return NOT_AVAILABLE
        used += 1
        if used > max_calls:
            return BUDGET_USED
        return None

    return gate

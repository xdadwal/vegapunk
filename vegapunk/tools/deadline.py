"""Bridge between tool handlers and the agent loop's enforced deadline.

Handlers that can block on external I/O register a terminator with the
logpose deadline handle; when ``tool_timeout`` fires, the loop runs it so the
handler is actually released instead of being abandoned on its worker thread.
Terminators must be thread-safe and must not block.
"""

from __future__ import annotations

from collections.abc import Callable

from logpose import current_tool_deadline


def register_terminator(terminator: Callable[[], None]) -> bool:
    """Register ``terminator`` to run if this invocation hits its deadline.

    Returns ``False`` when no deadline is in scope (``tool_timeout`` disabled
    or direct invocation outside the agent loop), so callers can skip setup.
    """
    handle = current_tool_deadline()
    if handle is None:
        return False
    handle.register_terminator(terminator)
    return True

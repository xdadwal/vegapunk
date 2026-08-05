"""The ``@tool`` decorator and the guarded-tool registry.

Write a normal, type-hinted function with a docstring, put ``@tool`` on it, and
it becomes a tool the model can call:

    @tool
    def get_weather(city: str) -> str:
        '''Look up the current weather for a city.

        Args:
            city: The city to look up.
        '''
        ...

The name comes from the function, the description from the docstring's summary,
the per-parameter descriptions from its ``Args:`` block, and the input schema is
derived from the type hints — no JSON Schema written by hand.

The schema work is logpose's; this module adds the one thing logpose has no
concept of, because it is Vegapunk's policy rather than the model's business:
which tools need a human's approval before they run. ``@tool(guarded=True)``
records the name in ``GUARDED``, which ``vegapunk.gate`` consults; read-only
tools keep the bare ``@tool``.
"""

from __future__ import annotations

from typing import Any, Callable, overload

from logpose import ToolDef
from logpose import tool as _logpose_tool

# Names of tools that must not run without approval. A set of names rather than
# a flag on the tool because logpose's ToolDef is the model's view of a tool —
# the gate is ours, and the model never sees it.
GUARDED: set[str] = set()

# Every @tool-decorated function lands here, in definition order.
# ``tools/__init__.py`` exposes it as ALL_TOOLS once the tool modules have been
# imported. Order is preserved rather than sorted: appending a tool then leaves
# the preceding request bytes untouched, which keeps prompt caching intact.
REGISTRY: list[ToolDef] = []


@overload
def tool(func: Callable[..., Any], /) -> ToolDef: ...


@overload
def tool(*, guarded: bool = ...) -> Callable[[Callable[..., Any]], ToolDef]: ...


def tool(func: Callable[..., Any] | None = None, /, *, guarded: bool = False) -> Any:
    """Register a function as a tool and return its ``ToolDef``.

    Works both bare and parameterized::

        @tool                    # read-only, runs freely
        def get_time() -> str: ...

        @tool(guarded=True)      # side-effecting, needs approval
        def write_file(path: str, content: str) -> str: ...

    The returned ``ToolDef`` is still an ordinary callable, so the function can
    be called directly from other code and from tests.
    """

    def decorate(fn: Callable[..., Any]) -> ToolDef:
        tool_def = _logpose_tool(fn)
        if guarded:
            GUARDED.add(tool_def.name)
        REGISTRY.append(tool_def)
        return tool_def

    return decorate(func) if func is not None else decorate

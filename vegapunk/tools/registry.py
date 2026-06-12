"""The ``@tool`` decorator and the tool registry.

Write a normal, type-hinted function with a docstring, put ``@tool`` on it, and
it becomes a Tool the model can call:

    @tool
    def get_weather(city: str) -> str:
        '''Look up the current weather for a city.'''
        ...

The name comes from the function, the description from the docstring, and the
input schema is *derived from the type hints* — no JSON Schema written by hand,
no manual registration.
"""

from __future__ import annotations

import inspect
from typing import Callable, get_type_hints

from .base import Tool

# Every @tool-decorated function lands here. ``tools/__init__.py`` exposes this
# list as ALL_TOOLS once the tool modules have been imported.
REGISTRY: list[Tool] = []

# How Python type hints map to JSON Schema types.
_JSON_TYPES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _build_parameters(func: Callable) -> dict:
    """Derive a JSON-Schema 'parameters' object from a function's signature."""
    signature = inspect.signature(func)
    hints = get_type_hints(func)

    properties: dict = {}
    required: list[str] = []
    for name, param in signature.parameters.items():
        hint = hints.get(name, str)  # treat unannotated params as strings
        properties[name] = {"type": _JSON_TYPES.get(hint, "string")}
        if param.default is inspect.Parameter.empty:
            required.append(name)  # no default -> the model must supply it

    return {"type": "object", "properties": properties, "required": required}


def tool(func: Callable[..., str]) -> Callable[..., str]:
    """Register ``func`` as a Tool and return it unchanged (still callable)."""
    valid_params = set(inspect.signature(func).parameters)

    def call(arguments: dict) -> str:
        # The model hands us a dict; the author wrote a normal function with
        # named params. Unpack into kwargs, ignoring any unexpected keys so a
        # slightly-off model call doesn't blow up.
        kwargs = {k: v for k, v in arguments.items() if k in valid_params}
        return func(**kwargs)

    REGISTRY.append(
        Tool(
            name=func.__name__,
            description=inspect.getdoc(func) or "",
            parameters=_build_parameters(func),
            func=call,
        )
    )

    return func

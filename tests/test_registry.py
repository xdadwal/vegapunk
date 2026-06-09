"""Tests for the @tool decorator — schema derivation and argument unpacking.

These run with no model and no network: we're testing the *tool-creation*
machinery itself, which is pure Python introspection.
"""

from vegapunk.tools.base import Tool
from vegapunk.tools.registry import REGISTRY, tool


def _last(name: str) -> Tool:
    """Return the most recently registered tool with this name."""
    return [t for t in REGISTRY if t.name == name][-1]


def test_schema_is_derived_from_signature():
    @tool
    def sample(city: str, days: int = 1) -> str:
        """Look up a forecast."""
        return f"{city} {days}"

    made = _last("sample")
    assert isinstance(made, Tool)
    assert made.name == "sample"
    assert made.description == "Look up a forecast."

    schema = made.to_schema()["function"]["parameters"]
    assert schema["properties"]["city"] == {"type": "string"}
    assert schema["properties"]["days"] == {"type": "integer"}
    assert schema["required"] == ["city"]  # days has a default, so it's optional


def test_tool_unpacks_arguments():
    @tool
    def greet(name: str) -> str:
        """Greet someone."""
        return f"hi {name}"

    made = _last("greet")
    # The model hands a dict; the Tool unpacks it into kwargs.
    assert made.run({"name": "Vegapunk"}) == "hi Vegapunk"
    # Unexpected keys are ignored rather than fatal.
    assert made.run({"name": "X", "bogus": 1}) == "hi X"

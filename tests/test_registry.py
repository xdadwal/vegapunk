"""Tests for the @tool decorator — schema derivation, invocation, guarding.

These run with no model and no network: we're testing the *tool-creation*
machinery itself, which is pure Python introspection.

The schema derivation is logpose's; what's pinned here is the contract Vegapunk
depends on (the shape the model is shown, and that a bad call comes back as
something the model can correct) plus the one thing Vegapunk adds — which tools
need approval.
"""

import asyncio

import pytest
from logpose import ToolDef, ToolExecutionError, ToolSchemaError

from vegapunk.tools.registry import GUARDED, REGISTRY, tool


def _last(name: str) -> ToolDef:
    """Return the most recently registered tool with this name."""
    return [t for t in REGISTRY if t.name == name][-1]


def _invoke(tool_def: ToolDef, arguments: dict) -> str:
    """Run a tool the way the loop does, and return what the model would see.

    Mirrors the loop's boundary: a bad call becomes a result the model can read
    and correct, never an exception that ends the turn.
    """
    try:
        return asyncio.run(tool_def.invoke(arguments))
    except ToolExecutionError as exc:
        return str(exc)


def test_schema_is_derived_from_signature():
    @tool
    def sample(city: str, days: int = 1) -> str:
        """Look up a forecast.

        Args:
            city: Which city to look up.
            days: How many days ahead.
        """
        return f"{city} {days}"

    made = _last("sample")
    assert isinstance(made, ToolDef)
    assert made.name == "sample"
    assert made.description == "Look up a forecast."

    schema = made.input_schema
    assert schema["properties"]["city"]["type"] == "string"
    assert schema["properties"]["days"]["type"] == "integer"
    assert schema["required"] == ["city"]  # days has a default, so it's optional


def test_parameter_docs_reach_the_model():
    # The Args: block is the only place a parameter's meaning is written down,
    # and a small model leans on it — so it has to land in the schema.
    @tool
    def documented(pattern: str) -> str:
        """Search for something.

        Args:
            pattern: A regular expression to match against each line.
        """
        return pattern

    schema = _last("documented").input_schema
    assert "regular expression" in schema["properties"]["pattern"]["description"]


def test_a_tool_is_still_an_ordinary_callable():
    @tool
    def greet(name: str) -> str:
        """Greet someone.

        Args:
            name: Who to greet.
        """
        return f"hi {name}"

    # Decorated or not, other code (and these tests) can just call it.
    assert greet("Vegapunk") == "hi Vegapunk"
    assert greet(name="Vegapunk") == "hi Vegapunk"


def test_arguments_arrive_coerced():
    @tool
    def repeat(word: str, times: int) -> str:
        """Repeat a word.

        Args:
            word: The word.
            times: How many times.
        """
        assert isinstance(times, int)
        return word * times

    # A small model often sends a number as a string; the tool still gets an int.
    assert _invoke(_last("repeat"), {"word": "ab", "times": "2"}) == "abab"


def test_a_missing_argument_comes_back_as_correctable_guidance():
    @tool
    def needs_one(path: str) -> str:
        """Read a path.

        Args:
            path: The path to read.
        """
        return path

    result = _invoke(_last("needs_one"), {})

    # The model has to be able to tell *which* argument it forgot.
    assert "path" in result


def test_an_unannotated_parameter_is_rejected_at_decoration():
    # Better a loud failure while writing the tool than a silently wrong schema
    # the model has to guess against at runtime.
    with pytest.raises(ToolSchemaError):

        @tool
        def sloppy(thing) -> str:  # noqa: ANN001 - deliberately unannotated
            """Do something."""
            return str(thing)


def test_guarded_tools_are_recorded_for_the_approval_gate():
    @tool(guarded=True)
    def danger() -> str:
        """Do something risky."""
        return "ok"

    @tool
    def safe() -> str:
        """Read something harmless."""
        return "ok"

    # @tool(guarded=True) marks the tool for the gate; bare @tool leaves it
    # free-running, and both still register and stay directly callable.
    assert "danger" in GUARDED
    assert "safe" not in GUARDED
    assert danger() == "ok" and safe() == "ok"


def test_the_real_tool_set_guards_exactly_the_side_effecting_tools():
    # The list that matters most in the whole suite: anything that writes to the
    # workspace or runs a command must not be able to run unapproved.
    from vegapunk.tools import ALL_TOOLS

    names = {t.name for t in ALL_TOOLS}
    assert {"write_file", "edit_file", "run_shell"} <= names
    assert GUARDED >= {"write_file", "edit_file", "run_shell"}

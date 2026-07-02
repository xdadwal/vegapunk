"""Tests for the agent loop's tool dispatch and event streaming — deterministic,
no model/network.

These pin the reflect-and-retry guidance in ``loop`` (a missing required
argument or an unknown tool name becomes a message worded to steer the model's
next turn, instead of a raw exception or a silent no-op), driven directly
through ``_run_tool_batch``. They also pin ``drive_turns``'s streaming
contract: text deltas re-yield upward, reasoning traces live to stderr, and
any returned reply was already yielded as deltas first (the display
invariant), driven through scripted event-stream brains.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from vegapunk.brain import Brain, BrainResponse, ReasoningDelta, TextDelta, ThinkEvent, ToolCall
from vegapunk.loop import _run_tool_batch, drive_turns
from vegapunk.tools.base import Tool


def _tool(name, func, *, required=None, properties=None, guarded=False) -> Tool:
    return Tool(
        name=name,
        description=name,
        parameters={
            "type": "object",
            "properties": properties or {},
            "required": required or [],
        },
        func=func,
        guarded=guarded,
    )


def _call(call_id: str, name: str, args: dict | None = None) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=args or {})


def test_missing_required_arg_returns_guidance_without_running():
    ran: list[dict] = []
    tool = _tool(
        "write_file",
        lambda a: ran.append(a) or "wrote",
        required=["path", "content"],
        properties={"path": {"type": "string"}, "content": {"type": "string"}},
    )
    results = _run_tool_batch({"write_file": tool}, [_call("c1", "write_file", {"path": "x"})])

    msg = results[0][1]
    assert "write_file" in msg
    assert "content" in msg  # names the argument that was left out
    assert ran == []  # short-circuited before the tool's function ran


def test_all_required_args_present_runs():
    tool = _tool(
        "write_file",
        lambda a: f"wrote {a['path']}",
        required=["path", "content"],
        properties={"path": {"type": "string"}, "content": {"type": "string"}},
    )
    results = _run_tool_batch(
        {"write_file": tool}, [_call("c1", "write_file", {"path": "x", "content": "hi"})]
    )
    assert results[0][1] == "wrote x"


def test_extra_args_are_tolerated():
    # The required-args check flags only MISSING required args, never extra ones —
    # a slightly-off call with a spurious key still runs.
    tool = _tool(
        "echo",
        lambda a: a.get("text", ""),
        required=["text"],
        properties={"text": {"type": "string"}},
    )
    results = _run_tool_batch({"echo": tool}, [_call("c1", "echo", {"text": "hi", "bogus": 1})])
    assert results[0][1] == "hi"


def test_no_required_args_tool_runs_with_empty_arguments():
    tool = _tool("get_time", lambda _a: "now")  # required is empty
    results = _run_tool_batch({"get_time": tool}, [_call("c1", "get_time", {})])
    assert results[0][1] == "now"


def test_unknown_tool_lists_available_tools():
    ran: list[dict] = []
    tool = _tool("get_time", lambda a: ran.append(a) or "now")
    results = _run_tool_batch({"get_time": tool}, [_call("c1", "fetch_url", {})])

    msg = results[0][1]
    assert "fetch_url" in msg  # echoes the invented name
    assert "get_time" in msg  # lists the real tool(s) so the model can recover
    assert ran == []  # the real tool was not run as a side effect


def test_required_arg_present_as_null_is_treated_as_supplied():
    # Lenient convention: presence is checked, not value. A required arg given
    # as null counts as supplied, so the tool runs (with None) rather than
    # short-circuiting — pinned so the choice is explicit.
    seen: list[dict] = []
    tool = _tool(
        "save",
        lambda a: seen.append(a) or "saved",
        required=["path"],
        properties={"path": {"type": "string"}},
    )
    results = _run_tool_batch({"save": tool}, [_call("c1", "save", {"path": None})])
    assert results[0][1] == "saved"
    assert seen == [{"path": None}]


def test_mixed_batch_preserves_order_with_concurrent_valid_calls():
    # Short-circuits (unknown tool, missing arg) interleaved with two valid calls
    # that run via the thread pool: results stay keyed to the original call order
    # and the short-circuited calls don't perturb the concurrent run.
    alpha = _tool("alpha", lambda _a: "A")
    beta = _tool("beta", lambda _a: "B")
    needs_arg = _tool(
        "write_file",
        lambda _a: "wrote",
        required=["content"],
        properties={"content": {"type": "string"}},
    )
    by_name = {"alpha": alpha, "beta": beta, "write_file": needs_arg}
    calls = [
        _call("c1", "fetch_url", {}),  # unknown -> short-circuit
        _call("c2", "write_file", {}),  # missing required 'content' -> short-circuit
        _call("c3", "alpha", {}),  # valid (runs concurrently)
        _call("c4", "beta", {}),  # valid (runs concurrently)
    ]
    results = _run_tool_batch(by_name, calls)

    assert [call.id for call, _ in results] == ["c1", "c2", "c3", "c4"]  # order preserved
    assert "fetch_url" in results[0][1] and "alpha" in results[0][1]  # unknown -> lists tools
    assert "content" in results[1][1]  # missing-arg guidance
    assert results[2][1] == "A"
    assert results[3][1] == "B"


class _ScriptedStreamBrain(Brain):
    """Plays back one scripted event stream per think() call."""

    def __init__(self, scripts: list[list[ThinkEvent]]) -> None:
        self._scripts = list(scripts)

    def think(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[ThinkEvent]:
        yield from self._scripts.pop(0)


def _response(text=None, tool_calls=None, reasoning=None, truncated=False) -> BrainResponse:
    message: dict = {"role": "assistant", "content": text}
    if tool_calls:
        message["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": "{}"}}
            for c in tool_calls
        ]
    return BrainResponse(
        message=message,
        text=text,
        tool_calls=tool_calls or [],
        reasoning=reasoning,
        truncated=truncated,
    )


def _drive(scripts, tools=None, max_steps=8):
    """Run drive_turns over scripted think() streams; return (yielded, reply)."""
    by_name = {t.name: t for t in tools or []}
    messages = [{"role": "system", "content": "SYS"}, {"role": "user", "content": "q"}]
    turns = drive_turns(_ScriptedStreamBrain(scripts), by_name, [], messages, max_steps)
    events = []
    while True:
        try:
            events.append(next(turns))
        except StopIteration as stop:
            return events, stop.value


def test_text_deltas_are_reyielded_and_the_reply_returned():
    events, reply = _drive([[TextDelta("It's "), TextDelta("2 PM."), _response("It's 2 PM.")]])
    assert events == [TextDelta("It's "), TextDelta("2 PM.")]
    assert reply == "It's 2 PM."


def test_reasoning_deltas_are_traced_live_to_stderr_not_reyielded(capsys):
    events, _reply = _drive(
        [[ReasoningDelta("pon"), ReasoningDelta("dering"), TextDelta("hi"), _response("hi")]]
    )
    assert events == [TextDelta("hi")]  # reasoning never reaches the reply stream
    err = capsys.readouterr().err
    assert "  [reason] pondering\n" in err  # fragments joined; line closed before the reply
    assert err.count("[reason]") == 1  # the prefix opens the line once


def test_reasoning_line_is_closed_when_the_stream_ends_without_text(capsys):
    # A pure tool-call turn ends its stream with reasoning still open; the
    # line must be closed so the [tool] trace doesn't glue onto it.
    ping = _tool("ping", lambda _a: "PONG")
    call = ToolCall(id="c1", name="ping", arguments={})
    _drive(
        [[ReasoningDelta("hmm"), _response(tool_calls=[call])], [_response("done")]],
        tools=[ping],
    )
    assert "  [reason] hmm\n" in capsys.readouterr().err


def test_truncated_turn_is_noted_on_the_trace(capsys):
    # finish_reason "length": the cut-off answer still flows through, but the
    # watch channel says so — a truncated reply is never passed off as chosen.
    events, reply = _drive([[TextDelta("half an ans"), _response("half an ans", truncated=True)]])
    assert reply == "half an ans"
    assert "cut off" in capsys.readouterr().err


def test_nonstreaming_response_text_is_still_yielded_as_a_delta():
    # A Brain that yields only its final response (no deltas) must still have
    # its answer displayed: the loop synthesizes one delta for it.
    events, reply = _drive([[_response("whole answer")]])
    assert events == [TextDelta("whole answer")]
    assert reply == "whole answer"


def test_spoken_text_before_tool_calls_gets_its_line_closed():
    ping = _tool("ping", lambda _a: "PONG")
    call = ToolCall(id="c1", name="ping", arguments={})
    events, reply = _drive(
        [
            [TextDelta("Checking..."), _response("Checking...", tool_calls=[call])],
            [TextDelta("done"), _response("done")],
        ],
        tools=[ping],
    )
    # The commentary line is closed with a newline before the tool trace runs.
    assert events == [TextDelta("Checking..."), TextDelta("\n"), TextDelta("done")]
    assert reply == "done"


def test_step_limit_notice_is_yielded_before_being_returned():
    ping = _tool("ping", lambda _a: "PONG")
    call = ToolCall(id="c1", name="ping", arguments={})
    tool_turn = [_response(tool_calls=[call])]
    events, reply = _drive([list(tool_turn[0:1]), [_response(tool_calls=[call])]],
                           tools=[ping], max_steps=2)
    assert "step limit" in reply.lower()
    assert events[-1] == TextDelta(reply)  # the notice reached the display stream too


def test_think_stream_without_a_final_response_fails_loudly():
    with pytest.raises(RuntimeError):
        _drive([[TextDelta("orphan")]])

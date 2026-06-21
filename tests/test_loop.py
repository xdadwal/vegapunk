"""Tests for the agent loop's tool dispatch — deterministic, no model/network.

These pin the reflect-and-retry guidance in ``loop``: a missing required
argument or an unknown tool name becomes a message worded to steer the model's
next turn, instead of a raw exception or a silent no-op. We drive the dispatch
directly through ``_run_tool_batch`` (single calls, so no thread pool) for
precise control over inputs and results.
"""

from __future__ import annotations

from vegapunk.brain import ToolCall
from vegapunk.loop import _run_tool_batch
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

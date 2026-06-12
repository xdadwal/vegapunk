"""Tests for multi-turn Session behavior — deterministic, no model/network/time.

We test the *plumbing* that lets a conversation persist, using a scripted
FakeBrain in place of a real model.
"""

from __future__ import annotations

import threading

from vegapunk.brain import Brain, BrainResponse, ToolCall
from vegapunk.loop import run
from vegapunk.session import Session
from vegapunk.tools import Tool


class FakeBrain(Brain):
    """A scripted Brain: returns queued responses in order, recording a copy of
    each messages list it was asked to think over."""

    def __init__(self, responses: list[BrainResponse]) -> None:
        self._responses = list(responses)
        self.seen_messages: list[list[dict]] = []

    def think(self, messages: list[dict], tools: list[dict] | None = None) -> BrainResponse:
        # Copy: drive_turns mutates the list in place, so snapshot what we saw.
        self.seen_messages.append(list(messages))
        return self._responses.pop(0)


def _text(content: str) -> BrainResponse:
    return BrainResponse(
        message={"role": "assistant", "content": content}, text=content, tool_calls=[]
    )


def test_history_persists_across_turns():
    fake = FakeBrain([_text("hi Akshay"), _text("your name is Akshay")])
    session = Session(fake, tools=[], system_prompt="SYS")

    assert session.send("my name is Akshay") == "hi Akshay"
    assert session.send("what's my name?") == "your name is Akshay"

    # On the 2nd think() call the brain saw the full prior history.
    second = [(m["role"], m.get("content")) for m in fake.seen_messages[1]]
    assert second == [
        ("system", "SYS"),
        ("user", "my name is Akshay"),
        ("assistant", "hi Akshay"),
        ("user", "what's my name?"),
    ]


def test_tool_call_turn_appends_assistant_then_tool_then_answers():
    call_turn = BrainResponse(
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "ping", "arguments": "{}"}}
            ],
        },
        text=None,
        tool_calls=[ToolCall(id="c1", name="ping", arguments={})],
    )
    fake = FakeBrain([call_turn, _text("pong received")])
    ping = Tool(
        name="ping",
        description="ping",
        parameters={"type": "object", "properties": {}, "required": []},
        func=lambda _args: "PONG",
    )
    session = Session(fake, tools=[ping], system_prompt="SYS")

    assert session.send("ping it") == "pong received"

    msgs = session.messages
    assert msgs[-3]["role"] == "assistant" and msgs[-3].get("tool_calls")  # the tool-call turn
    assert msgs[-2] == {"role": "tool", "tool_call_id": "c1", "content": "PONG"}  # observed result
    assert msgs[-1] == {"role": "assistant", "content": "pong received"}  # final answer


def test_system_prompt_seeded_once_and_survives_reset():
    fake = FakeBrain([_text("ok")])
    session = Session(fake, tools=[], system_prompt="SYS")
    assert session.messages[0] == {"role": "system", "content": "SYS"}

    session.send("hello")
    assert len(session.messages) > 1
    session.reset()
    assert session.messages == [{"role": "system", "content": "SYS"}]


def test_run_one_shot_still_works():
    # Guards the drive_turns extraction: the one-shot path is unchanged.
    fake = FakeBrain([_text("one-shot ok")])
    assert run(fake, [], "hello") == "one-shot ok"


def _simple_tool(name: str, func) -> Tool:
    return Tool(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}, "required": []},
        func=func,
    )


def _two_call_turn() -> BrainResponse:
    """An assistant turn where the model batches two tool calls at once."""
    return BrainResponse(
        message={
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "alpha", "arguments": "{}"}},
                {"id": "c2", "type": "function", "function": {"name": "beta", "arguments": "{}"}},
            ],
        },
        text=None,
        tool_calls=[
            ToolCall(id="c1", name="alpha", arguments={}),
            ToolCall(id="c2", name="beta", arguments={}),
        ],
    )


def test_batched_tool_results_keep_call_order():
    fake = FakeBrain([_two_call_turn(), _text("done")])
    session = Session(
        fake,
        tools=[_simple_tool("alpha", lambda _a: "A"), _simple_tool("beta", lambda _a: "B")],
        system_prompt="SYS",
    )

    assert session.send("both please") == "done"

    # Tool results line up with the assistant's tool_calls, in call order.
    assert session.messages[-3] == {"role": "tool", "tool_call_id": "c1", "content": "A"}
    assert session.messages[-2] == {"role": "tool", "tool_call_id": "c2", "content": "B"}
    assert session.messages[-1] == {"role": "assistant", "content": "done"}


def test_batched_tool_calls_run_concurrently():
    # Each tool blocks until the *other* one arrives at the barrier. Serial
    # execution would strand the first tool (barrier timeout -> error result);
    # concurrent execution lets both pass immediately.
    barrier = threading.Barrier(2)

    def wait_for_partner(_arguments: dict) -> str:
        barrier.wait(timeout=2)
        return "met"

    fake = FakeBrain([_two_call_turn(), _text("done")])
    session = Session(
        fake,
        tools=[
            _simple_tool("alpha", wait_for_partner),
            _simple_tool("beta", wait_for_partner),
        ],
        system_prompt="SYS",
    )
    session.send("go")

    tool_results = [m["content"] for m in session.messages if m["role"] == "tool"]
    assert tool_results == ["met", "met"]

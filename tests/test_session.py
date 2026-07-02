"""Tests for multi-turn Session behavior — deterministic, no model/network/time.

We test the *plumbing* that lets a conversation persist, using a scripted
FakeBrain in place of a real model.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

from vegapunk.brain import Brain, BrainResponse, ReasoningDelta, TextDelta, ThinkEvent, ToolCall
from vegapunk.loop import run
from vegapunk.session import Session
from vegapunk.tools import Tool


class FakeBrain(Brain):
    """A scripted Brain: streams queued responses in order, recording a copy of
    each messages list it was asked to think over.

    Mirrors the real streaming shape — deltas first (when the turn has them),
    then the assembled response, last — so tests exercise the same event path
    the CLI sees.
    """

    def __init__(self, responses: list[BrainResponse]) -> None:
        self._responses = list(responses)
        self.seen_messages: list[list[dict]] = []

    def think(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[ThinkEvent]:
        # Copy: drive_turns mutates the list in place, so snapshot what we saw.
        self.seen_messages.append(list(messages))
        response = self._responses.pop(0)
        if response.reasoning:
            yield ReasoningDelta(response.reasoning)
        if response.text:
            yield TextDelta(response.text)
        yield response


def _text(content: str) -> BrainResponse:
    return BrainResponse(
        message={"role": "assistant", "content": content}, text=content, tool_calls=[]
    )


def _reply(send_events) -> str:
    """Drain a send() stream and return the reply it carries in StopIteration."""
    while True:
        try:
            next(send_events)
        except StopIteration as stop:
            return stop.value


def test_history_persists_across_turns():
    fake = FakeBrain([_text("hi Akshay"), _text("your name is Akshay")])
    session = Session(fake, tools=[], system_prompt="SYS")

    assert _reply(session.send("my name is Akshay")) == "hi Akshay"
    assert _reply(session.send("what's my name?")) == "your name is Akshay"

    # On the 2nd think() call the brain saw the full prior history.
    second = [(m["role"], m.get("content")) for m in fake.seen_messages[1]]
    assert second == [
        ("system", "SYS"),
        ("user", "my name is Akshay"),
        ("assistant", "hi Akshay"),
        ("user", "what's my name?"),
    ]


def test_clarifying_question_then_continue():
    # The model can ask a clarifying question (a plain-text turn, no tools); the
    # user answers on the next turn and the model finishes with that context.
    # No new mechanism — this pins that the existing loop carries the back-and-forth.
    fake = FakeBrain(
        [
            _text("Which file do you mean — a.md or b.md?"),
            _text("Renamed a.md to archive.md."),
        ]
    )
    session = Session(fake, tools=[], system_prompt="SYS")

    assert _reply(session.send("rename the file")) == "Which file do you mean — a.md or b.md?"
    assert _reply(session.send("a.md")) == "Renamed a.md to archive.md."

    # On the 2nd think() the model saw its own question and the user's answer.
    second = [(m["role"], m.get("content")) for m in fake.seen_messages[1]]
    assert second == [
        ("system", "SYS"),
        ("user", "rename the file"),
        ("assistant", "Which file do you mean — a.md or b.md?"),
        ("user", "a.md"),
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

    assert _reply(session.send("ping it")) == "pong received"

    msgs = session.messages
    assert msgs[-3]["role"] == "assistant" and msgs[-3].get("tool_calls")  # the tool-call turn
    assert msgs[-2] == {"role": "tool", "tool_call_id": "c1", "content": "PONG"}  # observed result
    assert msgs[-1] == {"role": "assistant", "content": "pong received"}  # final answer


def test_reasoning_is_traced_to_stderr_but_kept_out_of_history(capsys):
    reasoning = "User asked who I am; the answer is in the system prompt."
    turn = BrainResponse(
        message={"role": "assistant", "content": "I'm Vegapunk."},
        text="I'm Vegapunk.",
        tool_calls=[],
        reasoning=reasoning,
    )
    session = Session(FakeBrain([turn]), tools=[], system_prompt="SYS")

    assert _reply(session.send("who are you?")) == "I'm Vegapunk."

    # Surfaced on the suppressible stderr watch-channel, beside [think]/[tool].
    assert f"  [reason] {reasoning}" in capsys.readouterr().err
    # ...but never replayed into the conversation history sent to the model.
    assert all(reasoning not in str(m) for m in session.messages)
    assert session.messages[-1] == {"role": "assistant", "content": "I'm Vegapunk."}


def test_system_prompt_seeded_once_and_survives_reset():
    fake = FakeBrain([_text("ok")])
    session = Session(fake, tools=[], system_prompt="SYS")
    assert session.messages[0] == {"role": "system", "content": "SYS"}

    _reply(session.send("hello"))
    assert len(session.messages) > 1
    session.reset()
    assert session.messages == [{"role": "system", "content": "SYS"}]


def test_run_one_shot_still_works():
    # Guards the drive_turns extraction: the one-shot path is unchanged.
    fake = FakeBrain([_text("one-shot ok")])
    assert run(fake, [], "hello") == "one-shot ok"


def test_restore_replaces_the_conversation():
    session = Session(FakeBrain([]), tools=[], system_prompt="SYS")
    saved = [
        {"role": "system", "content": "OLD"},
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a"},
    ]
    session.restore(saved)
    assert session.messages == saved  # faithful restore, including the saved system turn


def test_suggest_name_titles_first_user_message_without_touching_history():
    brain = FakeBrain([_text("Fixing the agent loop")])
    session = Session(brain, tools=[], system_prompt="SYS")
    session.restore(
        [{"role": "system", "content": "SYS"}, {"role": "user", "content": "the loop is broken"}]
    )

    assert session.suggest_name() == "Fixing the agent loop"
    # The titling call ran on a throwaway message list — history is untouched.
    assert session.messages == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "the loop is broken"},
    ]


def test_suggest_name_empty_when_no_user_turn_yet():
    # No user message -> returns "" without calling the model (queue stays full).
    session = Session(FakeBrain([]), tools=[], system_prompt="SYS")
    assert session.suggest_name() == ""


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

    assert _reply(session.send("both please")) == "done"

    # Tool results line up with the assistant's tool_calls, in call order.
    assert session.messages[-3] == {"role": "tool", "tool_call_id": "c1", "content": "A"}
    assert session.messages[-2] == {"role": "tool", "tool_call_id": "c2", "content": "B"}
    assert session.messages[-1] == {"role": "assistant", "content": "done"}


class _AlwaysToolBrain(Brain):
    """A Brain that never finishes — every turn requests a tool — so the loop
    runs until it hits the step budget. Records how many times it was asked."""

    def __init__(self) -> None:
        self.calls = 0

    def think(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[ThinkEvent]:
        self.calls += 1
        yield BrainResponse(
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


def test_session_honors_configured_max_steps():
    # The brain never returns a final answer, so the loop runs until the budget
    # is exhausted — it must stop after exactly max_steps think() calls.
    brain = _AlwaysToolBrain()
    session = Session(
        brain, tools=[_simple_tool("ping", lambda _a: "PONG")], system_prompt="SYS", max_steps=3
    )
    result = _reply(session.send("loop forever"))
    assert brain.calls == 3
    assert "step limit" in result.lower()


def test_session_default_max_steps_comes_from_config():
    # With no explicit max_steps, the budget is the configured default.
    from vegapunk.config import config

    brain = _AlwaysToolBrain()
    session = Session(brain, tools=[_simple_tool("ping", lambda _a: "PONG")], system_prompt="SYS")
    _reply(session.send("loop forever"))
    assert brain.calls == config.max_steps


def test_send_streams_the_reply_as_text_deltas():
    # The reply arrives as deltas *and* as the generator's return value — the
    # deltas are for live rendering, the return for programmatic callers.
    session = Session(FakeBrain([_text("hi Akshay")]), tools=[], system_prompt="SYS")

    events = session.send("hello")
    seen: list[TextDelta] = []
    while True:
        try:
            seen.append(next(events))
        except StopIteration as stop:
            reply = stop.value
            break

    assert seen == [TextDelta("hi Akshay")]
    assert reply == "hi Akshay"


def test_send_is_lazy_until_first_pull():
    # A created-but-never-consumed send must not touch history: generators run
    # nothing before the first next(), so the user turn isn't even appended.
    session = Session(FakeBrain([_text("unused")]), tools=[], system_prompt="SYS")
    session.send("hello")  # never pulled
    assert session.messages == [{"role": "system", "content": "SYS"}]


class _InterruptedBrain(Brain):
    """Streams a little text, then dies the way Ctrl-C lands mid-generation."""

    def think(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[ThinkEvent]:
        yield TextDelta("I was say")
        raise KeyboardInterrupt


def test_interrupt_mid_stream_rolls_the_partial_turn_back():
    session = Session(_InterruptedBrain(), tools=[], system_prompt="SYS")

    events = session.send("hi")
    with pytest.raises(KeyboardInterrupt):
        while True:
            next(events)

    # The half-generated turn (user message included) is rolled back out, so
    # the next send starts from a consistent history.
    assert session.messages == [{"role": "system", "content": "SYS"}]


def test_abandoning_the_stream_mid_turn_rolls_back():
    # The consumer stops pulling and closes the generator (what the CLI does
    # on Ctrl-C): the suspended send must roll back, not strand a half-turn.
    session = Session(FakeBrain([_text("a long answer")]), tools=[], system_prompt="SYS")

    events = session.send("hi")
    next(events)  # the turn is underway — a first delta arrived
    events.close()

    assert session.messages == [{"role": "system", "content": "SYS"}]


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
    _reply(session.send("go"))

    tool_results = [m["content"] for m in session.messages if m["role"] == "tool"]
    assert tool_results == ["met", "met"]

"""Tests for Session — the multi-turn conversation, against a scripted model.

What a Session owns is history: that it accumulates across turns, that the
system prompt is sent without ever becoming part of it, and — the contract that
matters most — that a turn which ends early leaves nothing behind. Ctrl-C, a
walked-away renderer, and a provider that blows up all take the same path, and
in all three the half-turn must be gone before the autosave can see it.

Driven by ``tests/fake_provider``: no model, no network, no credentials.
"""

from __future__ import annotations

import pytest
from logpose import Message, Usage, tool

from vegapunk.backend import Backend
from vegapunk.config import config
from vegapunk.loop import run
from vegapunk.session import Session
from tests.fake_provider import (
    FakeProvider,
    Turn,
    agent_for,
    backend_for,
    call,
    says,
    wants,
)


@tool
def ping() -> str:
    """Reply with pong."""
    return "pong"


@tool
def echo(text: str) -> str:
    """Echo some text.

    Args:
        text: What to echo.
    """
    return text


def _backend(turns, *, repeat_last: bool = False, title="a scripted title", **kwargs):
    provider = FakeProvider(turns, repeat_last=repeat_last)
    backend = Backend(
        provider=provider,
        model_label=kwargs.pop("model_label", "fake-model"),
        context_window=1000,
        # Its own provider: the titling agent runs on its own event loop.
        spawn_provider=lambda: FakeProvider(title, repeat_last=True),
        **kwargs,
    )
    return backend, provider


def _session(turns, *, tools=(), repeat_last: bool = False, title=None, **kwargs) -> Session:
    backend_kwargs = {key: kwargs.pop(key) for key in ("effort_key",) if key in kwargs}
    if title is not None:
        backend_kwargs["title"] = title
    backend, _provider = _backend(turns, repeat_last=repeat_last, **backend_kwargs)
    kwargs.setdefault("system_prompt", "SYS")
    return Session(backend, list(tools), **kwargs)


def _reply(send_events) -> str:
    """Drain a ``send`` generator and return the reply it finished with."""
    while True:
        try:
            next(send_events)
        except StopIteration as stop:
            return stop.value


# ---------------------------------------------------------------------------
# history
# ---------------------------------------------------------------------------


def test_history_persists_across_turns():
    session = _session([says("hi Akshay"), says("your name is Akshay")])

    _reply(session.send("my name is Akshay"))
    _reply(session.send("what is my name?"))

    roles = [m["role"] for m in session.messages]
    assert roles == ["user", "assistant", "user", "assistant"]
    assert session.messages[0]["content"][0]["text"] == "my name is Akshay"


def test_the_system_prompt_is_sent_every_turn_but_is_not_history():
    session = _session([says("ok"), says("still ok")])
    backend = session.backend

    _reply(session.send("first"))
    _reply(session.send("second"))

    assert all(request.system == "SYS" for request in backend.provider.requests)
    # It's the agent's, not a message — so it can't be edited by a restore or
    # counted as a turn.
    assert all(m["role"] != "system" for m in session.messages)


def test_a_tool_turn_records_the_call_and_its_result():
    session = _session([wants(call("ping")), says("pong received")], tools=[ping])

    assert _reply(session.send("ping please")) == "pong received"

    blocks = [b["type"] for m in session.messages for b in m["content"]]
    assert "tool_use" in blocks
    assert "tool_result" in blocks


def test_reasoning_is_traced_to_stderr_and_kept_out_of_the_reply(capsys):
    session = _session(says("the answer", thinking="deliberating"))

    reply = _reply(session.send("think about it"))

    assert "deliberating" in capsys.readouterr().err
    assert reply == "the answer"  # reasoning is a watch channel, not the answer
    assert session.last_reasoning == "deliberating"


def test_reasoning_stays_in_history_because_the_provider_requires_it():
    # Unlike the old hand-rolled brains, which dropped reasoning entirely: the
    # Anthropic API rejects a later turn whose thinking blocks were altered, so
    # the assistant turn is stored verbatim and replayed unchanged.
    session = _session([says("the answer", thinking="deliberating"), says("still here")])
    _reply(session.send("think about it"))

    _reply(session.send("again"))

    replayed = session.backend.provider.last_request.messages[1]
    assert [b.type for b in replayed.content] == ["thinking", "text"]
    assert replayed.content[0].thinking == "deliberating"


def test_reset_clears_the_conversation():
    session = _session([says("ok"), says("fresh")])
    _reply(session.send("first"))

    session.reset()

    assert session.messages == []
    assert session.context_tokens is None


def test_restore_replaces_the_conversation():
    session = _session(says("unused"))
    saved = [
        {"role": "user", "content": [{"type": "text", "text": "earlier question"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "earlier answer"}]},
    ]

    session.restore(saved)

    assert session.messages == saved
    assert session.context_tokens is None  # a saved session carries no count


def test_restore_rejects_a_message_it_cannot_parse():
    # The store checks the format first; this is the backstop behind it.
    session = _session(says("unused"))

    with pytest.raises(ValueError):
        session.restore([{"role": "system", "content": "not a block list"}])


def test_run_one_shot_still_works():
    agent, _provider = agent_for(says("one-shot ok"))

    assert run(agent, "hello") == "one-shot ok"


# ---------------------------------------------------------------------------
# streaming and laziness
# ---------------------------------------------------------------------------


def test_send_streams_the_reply_as_text_deltas():
    session = _session(says("hi Akshay", chunks=("hi ", "Akshay")))

    events = session.send("hello")
    deltas = []
    while True:
        try:
            deltas.append(next(events).text)
        except StopIteration as stop:
            assert stop.value == "hi Akshay"
            break

    assert deltas == ["hi ", "Akshay"]


def test_send_is_lazy_until_the_first_pull():
    session = _session(says("unused"))

    session.send("never consumed")  # created, never iterated

    # Nothing happened: no model call, and no user turn in history.
    assert session.messages == []
    assert session.backend.provider.call_count == 0


# ---------------------------------------------------------------------------
# rollback — the contract that keeps a half-turn out of the autosave
# ---------------------------------------------------------------------------


def test_an_interrupt_mid_turn_rolls_the_partial_turn_back():
    # Ctrl-C lands where the CLI is pulling, so it's thrown in at the yield —
    # the same place a real signal would surface.
    session = _session(says("a long answer", chunks=("a ", "long ", "answer")))

    events = session.send("interrupt me")
    next(events)
    with pytest.raises(KeyboardInterrupt):
        events.throw(KeyboardInterrupt())

    assert session.messages == []


def test_a_late_append_cannot_undo_the_rollback():
    """The rollback has to survive logpose finishing the turn behind our back.

    A real SIGINT unblocks the caller without cancelling the coroutine on
    logpose's loop thread, and that coroutine appends the assistant turn before
    the interrupt is ever seen. Truncating the live list in place would let that
    append land back on the cleaned history, leaving an assistant-first
    conversation the next request rejects.
    """
    session = _session(says("a long answer", chunks=("a ", "long ", "answer")))
    events = session.send("interrupt me")
    next(events)
    abandoned = session._conversation.messages  # what logpose is still holding

    with pytest.raises(KeyboardInterrupt):
        events.throw(KeyboardInterrupt())
    abandoned.append(Message.assistant_text("landed too late"))

    assert session.messages == []


def test_abandoning_the_stream_mid_turn_rolls_back():
    session = _session(says("a long answer", chunks=("a ", "long ", "answer")))

    events = session.send("start talking")
    next(events)  # consume one delta, then walk away
    events.close()

    assert session.messages == []


def test_a_failed_turn_is_rolled_out_of_history():
    session = _session(Turn(error=RuntimeError("provider is down")))

    with pytest.raises(RuntimeError):
        _reply(session.send("this will fail"))

    assert session.messages == []


def test_a_rolled_back_turn_leaves_earlier_turns_intact():
    session = _session([says("first answer"), Turn(error=RuntimeError("down"))])
    _reply(session.send("first"))

    with pytest.raises(RuntimeError):
        _reply(session.send("second"))

    assert [m["role"] for m in session.messages] == ["user", "assistant"]
    assert session.messages[0]["content"][0]["text"] == "first"


# ---------------------------------------------------------------------------
# limits, footprint, and the model itself
# ---------------------------------------------------------------------------


def test_session_honors_the_configured_step_limit():
    session = _session(wants(call("ping")), tools=[ping], repeat_last=True, max_steps=3)

    _reply(session.send("loop forever"))

    assert session.backend.provider.call_count == 3


def test_the_step_limit_defaults_to_config():
    session = _session(says("ok"))

    assert session._max_steps == config.max_steps


def test_send_records_the_context_footprint():
    session = _session(says("hi", usage=Usage(input_tokens=200, output_tokens=40)))

    _reply(session.send("hello"))

    assert session.context_tokens == 240


def test_reset_clears_a_stale_footprint():
    session = _session([says("hi", usage=Usage(input_tokens=240))])
    _reply(session.send("hello"))
    assert session.context_tokens == 240

    session.reset()

    assert session.context_tokens is None


def test_the_toolbar_reads_the_model_from_the_backend():
    session = _session(says("ok"))

    assert session.model_label == "fake-model"
    assert session.context_window == 1000


def test_swapping_the_backend_keeps_the_conversation():
    session = _session([says("hi", usage=Usage(input_tokens=240))])
    _reply(session.send("hello"))
    replacement, provider = _backend(says("still here"))

    session.swap_backend(replacement)

    assert session.model_label == replacement.model_label
    # The old number described the old model's context.
    assert session.context_tokens is None
    assert _reply(session.send("are you there?")) == "still here"
    # The conversation continued rather than restarting.
    assert len(provider.last_request.messages) == 3


def test_swapping_the_model_leaves_the_old_model_s_thinking_behind():
    # Regression: a conversation that began on a thinking model (the local
    # reasoning ones produce unsigned thinking blocks) and then moved to Claude
    # failed on every later turn with
    # "messages.N.content.0.thinking.signature: Field required" — permanently,
    # until /new. Thinking is display-only here, so it doesn't make the trip.
    session = _session([says("the answer", thinking="deliberating")])
    _reply(session.send("think about it"))
    replacement, provider = _backend(says("still here"))

    session.swap_backend(replacement)

    assert _reply(session.send("again")) == "still here"
    replayed = provider.last_request.messages[1]
    assert [b.type for b in replayed.content] == ["text"]


def test_resuming_a_saved_conversation_drops_thinking_it_cannot_vouch_for():
    # A saved session records no model, so its thinking can't be assumed
    # replayable to whatever is live now — and an assistant turn left with no
    # content at all is itself a 400, so it goes rather than travels empty.
    session = _session(says("ok"))

    session.restore(
        [
            {"role": "user", "content": [{"type": "text", "text": "hi"}]},
            {"role": "assistant", "content": [{"type": "thinking", "thinking": "hmm"}]},
            {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": "hello"},
                ],
            },
        ]
    )

    assert [[b["type"] for b in m["content"]] for m in session.messages] == [["text"], ["text"]]


def test_setting_effort_keeps_the_conversation_running():
    # Regression: /effort used to rebuild the agent, which handed the shared
    # provider's HTTP client to a new event loop and closed the old one — every
    # later turn then died with "Event loop is closed".
    session = _session([says("first"), says("second")], effort_key="output_config")
    _reply(session.send("hello"))
    agent_before = session._agent

    session.set_effort("low")

    assert session._agent is agent_before  # same agent, same loop
    assert session._agent.extra == {"output_config": {"effort": "low"}}
    assert _reply(session.send("still there?")) == "second"


def test_setting_effort_is_refused_on_a_backend_without_one():
    session = _session(says("ok"))

    with pytest.raises(ValueError, match="no effort setting"):
        session.set_effort("high")


def test_tools_are_advertised_to_the_model():
    session = _session(says("ok"), tools=[ping, echo])

    _reply(session.send("hello"))

    assert [spec.name for spec in session.backend.provider.last_request.tools] == ["ping", "echo"]


# ---------------------------------------------------------------------------
# titling
# ---------------------------------------------------------------------------


def test_suggest_name_titles_the_first_user_message_without_touching_history():
    session = _session(says("ok"), title=says("Fixing the agent loop"))
    _reply(session.send("help me fix the agent loop"))
    before = session.messages

    assert session.suggest_name() == "Fixing the agent loop"
    assert session.messages == before  # the probe ran on its own conversation


def test_the_titler_never_shares_the_conversation_s_provider():
    # Two agents means two event loops, and a provider's client belongs to the
    # first loop that touched it — sharing one makes every titling call fail.
    session = _session(says("ok"), title=says("A Title"))
    _reply(session.send("hello"))

    session.suggest_name()

    assert session._titler.provider is not session.backend.provider


def test_suggest_name_is_empty_before_the_first_turn():
    assert _session(says("unused")).suggest_name() == ""


def test_a_failed_titling_call_never_breaks_the_turn(capsys):
    session = _session(says("ok"), title=Turn(error=RuntimeError("titler is down")))
    _reply(session.send("hello"))

    assert session.suggest_name() == ""  # falls back, doesn't raise
    # ...but says so: a title that silently never works looks exactly like a
    # model that just picks bad titles.
    assert "could not title" in capsys.readouterr().err


def test_closing_a_session_is_safe_to_call_twice():
    session = _session(says("ok"))

    session.close()
    session.close()


def test_swapping_backends_drops_the_previous_backend_reasoning_encoding():
    """The bug this pins: a conversation that visited Codex and then switched to
    Claude failed on *every* later turn, permanently, until /new.

    The Responses backends encode reasoning as a ``RawBlock`` holding an opaque
    ``encrypted_content`` blob — never a ``ThinkingBlock`` — so the swap's
    thinking-block filter walked straight past it and handed Anthropic content
    it never signed and cannot parse. A fresh conversation on the same model
    worked, which is what made it read as a Claude fault rather than a swap one.
    """
    from logpose import Message, RawBlock, TextBlock

    session = _session([says("first")])
    session.restore(
        [
            Message.user("hi").model_dump(mode="json"),
            Message(
                role="assistant",
                content=[
                    RawBlock(data={"type": "reasoning", "encrypted_content": "opaque"}),
                    TextBlock(text="hello"),
                ],
            ).model_dump(mode="json"),
        ]
    )

    session.swap_backend(backend_for(says("second"), model_label="other-model"))

    blocks = [block for message in session.messages for block in message["content"]]
    assert not any(block.get("type") == "raw" for block in blocks)
    assert any(block.get("type") == "text" for block in blocks)  # the answer survives

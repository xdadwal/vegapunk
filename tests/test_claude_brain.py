"""Tests for ClaudeBrain — deterministic, no subprocess/network.

Two seams, mirroring how test_brain.py stubs the OpenAI client:

- ``think()`` tests swap ``brain._stream_query`` for a function replaying
  scripted SDK messages (the real claude-agent-sdk dataclasses — plain and
  offline-constructible), and record the (prompt, options) it was called with.
- Bridge tests monkeypatch ``vegapunk.claude_brain.query`` with a scripted
  async generator to pin the thread/queue relay: ordering, error propagation,
  and cancel-on-close.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock

from vegapunk.brain import BrainResponse, ReasoningDelta, TextDelta, create_brain
from vegapunk.claude_brain import ClaudeBrain
from vegapunk.config import config

FENCE = '```vega_tool\n{"name": "clock", "arguments": {"zone": "UTC"}}\n```\n'


def _text_event(text: str) -> StreamEvent:
    return StreamEvent(
        uuid="u",
        session_id="s",
        event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
    )


def _thinking_event(thought: str) -> StreamEvent:
    return StreamEvent(
        uuid="u",
        session_id="s",
        event={"type": "content_block_delta", "delta": {"type": "thinking_delta", "thinking": thought}},
    )


def _stop_event(stop_reason: str) -> StreamEvent:
    return StreamEvent(
        uuid="u",
        session_id="s",
        event={"type": "message_delta", "delta": {"stop_reason": stop_reason}},
    )


def _result(usage=None, is_error=False, subtype="success", result_text=None) -> ResultMessage:
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id="s",
        usage=usage,
        result=result_text,
    )


def _scripted(sdk_messages, cfg=None) -> tuple[ClaudeBrain, dict]:
    """A ClaudeBrain whose stream seam replays the given SDK messages."""
    brain = ClaudeBrain(cfg or config)
    recorded: dict = {}

    def fake_stream(prompt, options):
        recorded["prompt"] = prompt
        recorded["options"] = options

        def _replay():  # a real generator, like the bridge: think() closes it
            yield from list(sdk_messages)

        return _replay()

    brain._stream_query = fake_stream  # swap the bridge for the script
    return brain, recorded


def _messages(user="hello") -> list[dict]:
    return [{"role": "system", "content": "SYS"}, {"role": "user", "content": user}]


def test_text_deltas_stream_live_and_join_into_the_response():
    brain, _ = _scripted([_text_event("4"), _text_event("2"), _result()])
    events = list(brain.think(_messages()))

    assert events[:2] == [TextDelta("4"), TextDelta("2")]
    response = events[-1]
    assert isinstance(response, BrainResponse)
    assert response.text == "42"
    assert response.message == {"role": "assistant", "content": "42"}
    assert response.tool_calls == []


def test_a_fence_becomes_a_tool_call_and_never_reaches_the_display():
    brain, _ = _scripted([_text_event("On it.\n"), _text_event(FENCE), _result()])
    events = list(brain.think(_messages()))

    deltas = [e.text for e in events if isinstance(e, TextDelta)]
    assert "".join(deltas) == "On it.\n"  # the fence never streamed as text
    response = events[-1]
    assert response.text == "On it.\n"
    [call] = response.tool_calls
    assert call.name == "clock"
    assert call.arguments == {"zone": "UTC"}
    assert call.id.startswith("call_")
    [wire_call] = response.message["tool_calls"]
    assert wire_call["function"]["name"] == "clock"
    # OpenAI wire shape: arguments re-serialized as a JSON string.
    assert json.loads(wire_call["function"]["arguments"]) == {"zone": "UTC"}
    assert wire_call["id"] == call.id


def test_thinking_deltas_surface_as_reasoning_but_stay_out_of_history():
    brain, _ = _scripted(
        [_thinking_event("hmm"), _thinking_event(" ok"), _text_event("done"), _result()]
    )
    events = list(brain.think(_messages()))

    assert events[0] == ReasoningDelta("hmm")
    response = events[-1]
    assert response.reasoning == "hmm ok"
    assert "reasoning" not in response.message
    assert response.message["content"] == "done"


def test_usage_sums_into_context_tokens():
    usage = {
        "input_tokens": 10,
        "cache_read_input_tokens": 100,
        "cache_creation_input_tokens": 5,
        "output_tokens": 7,
    }
    brain, _ = _scripted([_text_event("hi"), _result(usage=usage)])
    response = list(brain.think(_messages()))[-1]
    assert response.context_tokens == 122


def test_missing_usage_leaves_context_tokens_none():
    brain, _ = _scripted([_text_event("hi"), _result(usage=None)])
    assert list(brain.think(_messages()))[-1].context_tokens is None


def test_max_tokens_stop_reason_marks_the_turn_truncated():
    brain, _ = _scripted([_text_event("cut off"), _stop_event("max_tokens"), _result()])
    assert list(brain.think(_messages()))[-1].truncated is True


def test_fallback_path_still_detects_truncation():
    # No stream events at all — stop_reason must be read off the assembled
    # message, or a max_tokens cut would pass as a complete reply.
    assembled = AssistantMessage(content=[TextBlock(text="cut of")], model="m", stop_reason="max_tokens")
    brain, _ = _scripted([assembled, _result()])
    response = list(brain.think(_messages()))[-1]
    assert response.text == "cut of"
    assert response.truncated is True


def test_result_message_stop_reason_also_marks_truncation():
    result = _result()
    result.stop_reason = "max_tokens"
    brain, _ = _scripted([_text_event("cut of"), result])
    assert list(brain.think(_messages()))[-1].truncated is True


def test_error_result_raises_with_the_actionable_auth_hint():
    brain, _ = _scripted(
        [_result(is_error=True, subtype="error_during_execution", result_text="not logged in")]
    )
    with pytest.raises(RuntimeError) as excinfo:
        list(brain.think(_messages()))
    message = str(excinfo.value)
    assert "not logged in" in message
    assert "claude /login" in message
    assert "CLAUDE_CODE_OAUTH_TOKEN" in message


def test_error_max_turns_is_a_normal_single_turn_completion():
    brain, _ = _scripted([_text_event("reply"), _result(is_error=True, subtype="error_max_turns")])
    assert list(brain.think(_messages()))[-1].text == "reply"


def test_stream_without_a_result_raises_loudly():
    brain, _ = _scripted([_text_event("half a rep")])
    with pytest.raises(RuntimeError, match="without a result"):
        list(brain.think(_messages()))


def test_assembled_message_recovers_the_turn_when_no_deltas_arrived():
    assembled = AssistantMessage(content=[TextBlock(text="hi there")], model="m")
    brain, _ = _scripted([assembled, _result()])
    events = list(brain.think(_messages()))
    assert [e.text for e in events if isinstance(e, TextDelta)] == ["hi there"]
    assert events[-1].text == "hi there"


def test_assembled_message_is_ignored_when_deltas_already_streamed():
    assembled = AssistantMessage(content=[TextBlock(text="streamed")], model="m")
    brain, _ = _scripted([_text_event("streamed"), assembled, _result()])
    response = list(brain.think(_messages()))[-1]
    assert response.text == "streamed"  # not doubled


def test_options_pin_the_isolation_and_single_turn_contract():
    brain, recorded = _scripted([_text_event("hi"), _result()])
    tools = [
        {
            "type": "function",
            "function": {"name": "clock", "description": "Time.", "parameters": {}},
        }
    ]
    list(brain.think(_messages(), tools=tools))

    options = recorded["options"]
    assert options.max_turns == 1
    assert options.tools == []  # every Claude Code built-in disabled
    assert options.include_partial_messages is True
    assert options.setting_sources == []  # no user CLAUDE.md/settings bleed-in
    assert options.skills == []
    assert options.strict_mcp_config is True
    assert options.system_prompt.startswith("SYS")
    assert "```vega_tool" in options.system_prompt  # the tool protocol rode along
    assert "[user]\nhello" in recorded["prompt"]


def test_tool_stanza_is_omitted_when_there_are_no_tools():
    brain, recorded = _scripted([_text_event("hi"), _result()])
    list(brain.think(_messages()))
    assert "vega_tool" not in recorded["options"].system_prompt


def test_think_is_lazy_until_first_pull():
    brain, recorded = _scripted([_text_event("hi"), _result()])
    events = brain.think(_messages())  # not consumed
    assert recorded == {}  # nothing sent yet
    next(events)
    assert "prompt" in recorded


def test_identity_comes_from_claude_config_fields():
    cfg = replace(config, claude_model="opus", claude_context_window=123456)
    brain = ClaudeBrain(cfg)
    assert brain.model_label == "claude:opus"
    assert brain.context_window == 123456
    assert ClaudeBrain(replace(config, claude_model="")).model_label == "claude"


def test_create_brain_builds_the_claude_provider():
    assert isinstance(create_brain("claude"), ClaudeBrain)


def test_closing_think_mid_stream_closes_the_underlying_stream():
    # Ctrl-C teardown: think()'s finally must close the bridge stream (which
    # in production cancels the async task and reaps the CLI subprocess).
    state = {"closed": False}
    brain = ClaudeBrain(config)

    def fake_stream(prompt, options):
        def _replay():
            try:
                yield _text_event("he")
                yield _text_event("llo")
                yield _result()
            finally:
                state["closed"] = True

        return _replay()

    brain._stream_query = fake_stream
    events = brain.think(_messages())
    next(events)  # the stream is live
    events.close()
    assert state["closed"] is True


# --- the async→sync bridge ---------------------------------------------------


def test_bridge_relays_messages_in_order_and_ends(monkeypatch):
    async def scripted_query(*, prompt, options):
        yield "one"
        yield "two"

    monkeypatch.setattr("vegapunk.claude_brain.query", scripted_query)
    brain = ClaudeBrain(config)
    assert list(brain._stream_query("p", None)) == ["one", "two"]


def test_bridge_surfaces_async_errors_with_context(monkeypatch):
    async def failing_query(*, prompt, options):
        yield "one"
        raise ValueError("boom")

    monkeypatch.setattr("vegapunk.claude_brain.query", failing_query)
    brain = ClaudeBrain(config)
    events = brain._stream_query("p", None)
    assert next(events) == "one"
    with pytest.raises(RuntimeError) as excinfo:
        next(events)
    assert "boom" in str(excinfo.value)
    assert isinstance(excinfo.value.__cause__, ValueError)


def test_bridge_close_cancels_the_async_side_and_joins_the_worker(monkeypatch):
    state = {"closed": False}

    async def parked_query(*, prompt, options):
        try:
            yield "tick"
            await asyncio.Event().wait()  # parks until cancelled
        finally:
            state["closed"] = True

    monkeypatch.setattr("vegapunk.claude_brain.query", parked_query)
    brain = ClaudeBrain(config)
    events = brain._stream_query("p", None)
    assert next(events) == "tick"

    events.close()  # must cancel the parked task and join the thread

    assert state["closed"] is True

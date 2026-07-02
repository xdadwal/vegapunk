"""Tests for DMRBrain's stream parsing — deterministic, no model/network.

``think`` is a generator: it must yield ``ReasoningDelta``/``TextDelta``
fragments in arrival order and end with the assembled ``BrainResponse``. These
pin that assembly — fragmented tool-call arguments, the reasoning field's
capture-but-never-replay contract, malformed-JSON recovery — by driving
``think`` with a stub client that plays back scripted stream chunks.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vegapunk.brain import (
    BrainResponse,
    DMRBrain,
    ReasoningDelta,
    TextDelta,
    final_response,
)


def _chunk(finish_reason=None, **delta_fields):
    """A stream chunk whose single choice carries the given delta fields.

    ``reasoning_content`` is set only when passed, mirroring a server that
    omits the field when absent (the brain reads it with getattr).
    """
    delta = SimpleNamespace(content=None, tool_calls=None)
    for key, value in delta_fields.items():
        setattr(delta, key, value)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)])


def _call_fragment(index, id=None, name=None, arguments=None):
    """One tool-call fragment as it appears on a chunk's delta."""
    return SimpleNamespace(
        index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments)
    )


class _StubStream:
    """Stands in for the SDK's Stream: a context manager you can iterate."""

    def __init__(self, chunks) -> None:
        self._chunks = chunks
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.closed = True
        return False

    def __iter__(self):
        return iter(self._chunks)


class _StubClient:
    """A chat-completions client that plays back one scripted stream."""

    def __init__(self, chunks) -> None:
        self.stream = _StubStream(chunks)
        self.last_kwargs: dict | None = None
        chat = type("Chat", (), {})()
        chat.completions = type("Completions", (), {"create": self._create})()
        self.chat = chat

    def _create(self, **kwargs):
        self.last_kwargs = kwargs
        return self.stream


def _brain_streaming(chunks, finish="stop") -> DMRBrain:
    """A DMRBrain playing back scripted chunks. A terminal finish_reason chunk
    is appended by default (a well-behaved server always sends one); pass
    ``finish=None`` to simulate a stream that dies without finishing."""
    if finish is not None:
        chunks = [*chunks, _chunk(finish_reason=finish)]
    brain = DMRBrain()
    brain._client = _StubClient(chunks)  # swap the real client for the stub
    return brain


def _events(brain: DMRBrain, messages=None, tools=None):
    return list(brain.think(messages or [{"role": "user", "content": "x"}], tools=tools))


def test_text_deltas_are_yielded_live_and_joined_into_the_response():
    brain = _brain_streaming([_chunk(content="4"), _chunk(content="2")])
    events = _events(brain)

    assert events[:2] == [TextDelta("4"), TextDelta("2")]
    response = events[-1]
    assert isinstance(response, BrainResponse)  # the stream always ends with the turn
    assert response.text == "42"
    assert response.message == {"role": "assistant", "content": "42"}


def test_reasoning_deltas_are_yielded_live_and_captured_on_the_response():
    brain = _brain_streaming(
        [_chunk(reasoning_content="3 * 14"), _chunk(reasoning_content=" = 42"), _chunk(content="42")]
    )
    events = _events(brain)

    assert events[:2] == [ReasoningDelta("3 * 14"), ReasoningDelta(" = 42")]
    assert events[-1].reasoning == "3 * 14 = 42"
    assert events[-1].text == "42"


def test_reasoning_is_kept_out_of_the_replayed_history_turn():
    brain = _brain_streaming(
        [_chunk(reasoning_content="secret chain of thought"), _chunk(content="42")]
    )
    response = final_response(brain.think([{"role": "user", "content": "x"}]))

    # The message replayed to the model must carry only the fields the server
    # expects — never the chain-of-thought.
    assert response.message == {"role": "assistant", "content": "42"}
    assert "secret chain of thought" not in str(response.message)


def test_reasoning_is_none_when_the_server_omits_the_field():
    response = final_response(_brain_streaming([_chunk(content="hi")]).think([]))
    assert response.reasoning is None


def test_blank_reasoning_normalizes_to_none():
    # A whitespace-only chain of thought is treated as absent on the response.
    response = final_response(
        _brain_streaming([_chunk(reasoning_content="   \n  "), _chunk(content="hi")]).think([])
    )
    assert response.reasoning is None


def test_tool_call_arguments_fragmented_across_chunks_are_assembled():
    # OpenAI-style streaming: id/name arrive on the first fragment, the
    # argument JSON dribbles in across later ones, all keyed by index.
    brain = _brain_streaming(
        [
            _chunk(tool_calls=[_call_fragment(0, id="c1", name="write_file", arguments="")]),
            _chunk(tool_calls=[_call_fragment(0, arguments='{"path": ')]),
            _chunk(tool_calls=[_call_fragment(0, arguments='"a.md"}')]),
        ]
    )
    response = final_response(brain.think([]))

    assert len(response.tool_calls) == 1
    call = response.tool_calls[0]
    assert (call.id, call.name, call.arguments) == ("c1", "write_file", {"path": "a.md"})
    # The replayed turn carries the raw joined argument string.
    assert response.message["tool_calls"] == [
        {
            "id": "c1",
            "type": "function",
            "function": {"name": "write_file", "arguments": '{"path": "a.md"}'},
        }
    ]


def test_whole_tool_call_in_a_single_chunk_works():
    # Docker Model Runner sends each call complete in one chunk — the
    # accumulate-by-index path must handle that shape identically.
    brain = _brain_streaming(
        [_chunk(tool_calls=[_call_fragment(0, id="c1", name="get_time", arguments="{}")])]
    )
    response = final_response(brain.think([]))

    assert response.tool_calls[0].name == "get_time"
    assert response.tool_calls[0].arguments == {}
    assert response.text is None  # no content chunks -> no text
    assert response.message["content"] is None


def test_two_tool_calls_keep_their_own_slots_by_index():
    brain = _brain_streaming(
        [
            _chunk(tool_calls=[_call_fragment(0, id="c1", name="alpha", arguments="{}")]),
            _chunk(tool_calls=[_call_fragment(1, id="c2", name="beta", arguments="{}")]),
        ]
    )
    response = final_response(brain.think([]))

    assert [(c.id, c.name) for c in response.tool_calls] == [("c1", "alpha"), ("c2", "beta")]


def test_malformed_tool_arguments_fall_back_to_empty_dict():
    # A small model can emit broken argument JSON; the call still surfaces
    # (with empty args) instead of crashing the loop.
    brain = _brain_streaming(
        [_chunk(tool_calls=[_call_fragment(0, id="c1", name="save", arguments="{not json")])]
    )
    response = final_response(brain.think([]))

    assert response.tool_calls[0].arguments == {}


def test_chunks_without_choices_are_skipped():
    # Some servers append a usage-only chunk with an empty choices list.
    chunks = [_chunk(content="ok"), SimpleNamespace(choices=[])]
    response = final_response(_brain_streaming(chunks).think([]))
    assert response.text == "ok"


def test_request_streams_and_passes_tools_only_when_given():
    brain = _brain_streaming([_chunk(content="hi")])
    _events(brain)  # no tools
    assert brain._client.last_kwargs["stream"] is True
    assert "tools" not in brain._client.last_kwargs

    brain = _brain_streaming([_chunk(content="hi")])
    schema = [{"type": "function", "function": {"name": "t"}}]
    _events(brain, tools=schema)
    assert brain._client.last_kwargs["tools"] == schema


def test_stream_is_closed_when_generation_completes():
    brain = _brain_streaming([_chunk(content="hi")])
    final_response(brain.think([]))
    assert brain._client.stream.closed  # the `with` released the connection


def test_final_response_raises_on_a_stream_with_no_response():
    # A think() stream that ends without its final event is a broken contract;
    # draining it must fail loudly, not return None.
    def broken():
        yield TextDelta("orphan")

    with pytest.raises(RuntimeError):
        final_response(broken())


def test_think_is_lazy_until_first_pull():
    brain = _brain_streaming([_chunk(content="hi")])
    brain.think([{"role": "user", "content": "x"}])  # created but never pulled
    assert brain._client.last_kwargs is None  # nothing was sent to the server


def test_stream_is_closed_when_the_generator_is_abandoned():
    # The consumer stops pulling mid-turn (Ctrl-C path): the `with` must still
    # release the HTTP stream, not leave the connection dangling.
    brain = _brain_streaming([_chunk(content="a"), _chunk(content="b")])
    events = brain.think([])
    next(events)  # underway
    events.close()
    assert brain._client.stream.closed


def test_stream_ending_without_a_finish_reason_raises():
    # A server that disconnects mid-generation ends the stream without ever
    # saying the turn finished; the half-answer must not be passed off as
    # complete (it would be displayed, replayed, and saved to disk).
    brain = _brain_streaming([_chunk(content="half an ans")], finish=None)
    with pytest.raises(RuntimeError):
        final_response(brain.think([]))


def test_length_finish_marks_the_response_truncated():
    brain = _brain_streaming([_chunk(content="half")], finish="length")
    response = final_response(brain.think([]))
    assert response.truncated is True
    assert response.text == "half"  # the prefix is kept — just never passed off as an ending


def test_normal_stop_is_not_marked_truncated():
    response = final_response(_brain_streaming([_chunk(content="done")]).think([]))
    assert response.truncated is False


def test_interleaved_argument_fragments_stay_with_their_own_call():
    # Two concurrent calls whose argument JSON dribbles in alternately — the
    # per-index slots must not bleed into each other.
    chunks = [
        _chunk(tool_calls=[_call_fragment(0, id="c1", name="alpha", arguments='{"a"')]),
        _chunk(tool_calls=[_call_fragment(1, id="c2", name="beta", arguments='{"b"')]),
        _chunk(tool_calls=[_call_fragment(0, arguments=": 1}")]),
        _chunk(tool_calls=[_call_fragment(1, arguments=": 2}")]),
    ]
    response = final_response(_brain_streaming(chunks).think([]))
    assert [(c.name, c.arguments) for c in response.tool_calls] == [
        ("alpha", {"a": 1}),
        ("beta", {"b": 2}),
    ]


def test_out_of_order_indexes_assemble_in_index_order():
    # tool_calls must line up with the order the model declared them (index),
    # not the order fragments happened to arrive.
    chunks = [
        _chunk(tool_calls=[_call_fragment(1, id="c2", name="beta", arguments="{}")]),
        _chunk(tool_calls=[_call_fragment(0, id="c1", name="alpha", arguments="{}")]),
    ]
    response = final_response(_brain_streaming(chunks).think([]))
    assert [c.id for c in response.tool_calls] == ["c1", "c2"]


def test_fragment_without_a_function_still_records_the_id():
    # The spec allows a fragment that carries only index/id, with function None.
    chunks = [
        _chunk(tool_calls=[SimpleNamespace(index=0, id="c1", function=None)]),
        _chunk(tool_calls=[_call_fragment(0, name="alpha", arguments="{}")]),
    ]
    response = final_response(_brain_streaming(chunks).think([]))
    call = response.tool_calls[0]
    assert (call.id, call.name) == ("c1", "alpha")


def test_empty_string_content_yields_no_delta():
    # The initial role chunk often carries content "" or None — a renderer
    # must not receive an empty delta for it.
    events = _events(_brain_streaming([_chunk(content=""), _chunk(content="hi")]))
    assert events[0] == TextDelta("hi")

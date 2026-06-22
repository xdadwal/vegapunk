"""Tests for DMRBrain's response parsing — deterministic, no model/network.

These pin how the brain handles a reasoning model's separate ``reasoning_content``
field: it's captured onto ``BrainResponse.reasoning`` for display, but kept out of
the clean assistant turn that gets replayed into history. We drive ``think`` with a
stub client so no real server is touched.
"""

from __future__ import annotations

from vegapunk.brain import DMRBrain


class _StubMessage:
    """Stands in for the OpenAI SDK message. ``reasoning_content`` is passed only
    when a turn has it, mirroring a server that omits the field when absent."""

    def __init__(self, content, tool_calls=None, **extra) -> None:
        self.content = content
        self.tool_calls = tool_calls
        for key, value in extra.items():
            setattr(self, key, value)


class _StubClient:
    """A chat-completions client that always returns one queued message."""

    def __init__(self, message: _StubMessage) -> None:
        self._message = message
        chat = type("Chat", (), {})()
        chat.completions = type("Completions", (), {"create": self._create})()
        self.chat = chat

    def _create(self, **_kwargs):
        choice = type("Choice", (), {"message": self._message})()
        return type("Completion", (), {"choices": [choice]})()


def _brain_returning(message: _StubMessage) -> DMRBrain:
    brain = DMRBrain()
    brain._client = _StubClient(message)  # swap the real client for the stub
    return brain


def test_reasoning_content_is_captured_on_the_response():
    msg = _StubMessage(content="42", reasoning_content="3 * 14 = 42")
    response = _brain_returning(msg).think([{"role": "user", "content": "3*14?"}])

    assert response.reasoning == "3 * 14 = 42"
    assert response.text == "42"


def test_reasoning_is_kept_out_of_the_replayed_history_turn():
    msg = _StubMessage(content="42", reasoning_content="secret chain of thought")
    response = _brain_returning(msg).think([{"role": "user", "content": "x"}])

    # The message replayed to the model must carry only the fields the server
    # expects — never the chain-of-thought.
    assert response.message == {"role": "assistant", "content": "42"}
    assert "secret chain of thought" not in str(response.message)


def test_reasoning_is_none_when_the_server_omits_the_field():
    msg = _StubMessage(content="hi")  # no reasoning_content attribute at all
    response = _brain_returning(msg).think([{"role": "user", "content": "x"}])

    assert response.reasoning is None


def test_blank_reasoning_normalizes_to_none():
    # A whitespace-only field is treated as absent, so the loop won't print an
    # empty [reason] line.
    msg = _StubMessage(content="hi", reasoning_content="   \n  ")
    response = _brain_returning(msg).think([{"role": "user", "content": "x"}])

    assert response.reasoning is None

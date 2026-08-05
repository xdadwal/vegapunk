"""Reading a conversation back — which turns were the user's, and what they said.

History is a list of logpose messages, where a turn's ``content`` is a list of
typed blocks rather than a string. That matters more than it sounds: a turn's
tool *results* come back as a ``user`` message too, because that's how the
provider wants them. So "role == user" no longer means "a human said this", and
counting user messages would count the model's own tool traffic as conversation.

These helpers are the one place that distinction lives, so the session list, the
/history view, and the auto-namer all agree on what a turn is.

They read the persisted dict shape (``Message.model_dump()``) rather than the
model objects, so the same code serves a live conversation and a saved one.
"""

from __future__ import annotations

from typing import Any

Message = dict[str, Any]


def _blocks(message: Message) -> list[dict]:
    """The message's content blocks, or none if it isn't the shape we expect."""
    content = message.get("content")
    return [b for b in content if isinstance(b, dict)] if isinstance(content, list) else []


def is_user_turn(message: Message) -> bool:
    """Whether this message is something the human actually said.

    A user message carrying tool results is the loop talking to itself; a user
    message with text in it is a turn.
    """
    if message.get("role") != "user":
        return False
    blocks = _blocks(message)
    if any(block.get("type") == "tool_result" for block in blocks):
        return False
    return any(block.get("type") == "text" for block in blocks)


def text_of(message: Message) -> str:
    """Every text block in the message, joined. Non-text blocks are ignored."""
    return "".join(
        block.get("text", "") for block in _blocks(message) if block.get("type") == "text"
    )


def count_user_turns(messages: list[Message]) -> int:
    """How many turns the human took — what /sessions reports per conversation."""
    return sum(1 for message in messages if is_user_turn(message))


def recent_turns(messages: list[Message], limit: int) -> list[tuple[str, str | None]]:
    """The last ``limit`` exchanges as ``(user_text, reply_text)`` pairs.

    Pairs each user turn with the assistant's next spoken reply; tool traffic and
    tool-call-only assistant turns (no text) are skipped. A trailing user turn
    with no reply yet pairs with ``None``.
    """
    turns: list[tuple[str, str | None]] = []
    pending: str | None = None
    for message in messages:
        if is_user_turn(message):
            if pending is not None:
                turns.append((pending, None))
            pending = text_of(message)
        elif message.get("role") == "assistant" and pending is not None:
            reply = text_of(message)
            if reply:
                turns.append((pending, reply))
                pending = None
    if pending is not None:
        turns.append((pending, None))
    return turns[-limit:]

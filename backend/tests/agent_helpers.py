"""A scripted stand-in for the AI model, and the wiring to run the operator against it."""

import asyncio
import itertools
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from cryptography.fernet import Fernet
from logpose import Agent
from logpose.messages import Message, TextBlock, ToolUseBlock, Usage
from logpose.providers.base import (
    CompletionDone,
    CompletionRequest,
    ProviderEvent,
    ProviderTextDelta,
)

from vegapunk.ai.operator import Operator
from vegapunk.ai.store import save_ai
from vegapunk.config import Settings
from vegapunk.db import connect

CRYPTO_KEY = Fernet.generate_key()
API_KEY = "sk-test-key-abcdef123456"
_ids = itertools.count(1)


@dataclass(frozen=True)
class Turn:
    """One model round trip."""

    message: Message = field(default_factory=lambda: Message(role="assistant", content=[]))
    stop_reason: str = "end_turn"
    deltas: tuple[ProviderEvent, ...] = ()
    error: BaseException | None = None
    delay: float = 0.0

    @classmethod
    def text(cls, text: str, chunks: list[str] | None = None) -> "Turn":
        parts = [text] if chunks is None else chunks
        return cls(
            message=Message(role="assistant", content=[TextBlock(text=text)]),
            deltas=tuple(ProviderTextDelta(text=c) for c in parts if c),
        )

    @classmethod
    def tool(
        cls,
        name: str,
        args: dict[str, Any] | None = None,
        *,
        id: str | None = None,
        text: str | None = None,
    ) -> "Turn":
        blocks: list[Any] = []
        deltas: list[ProviderEvent] = []
        if text:
            blocks.append(TextBlock(text=text))
            deltas.append(ProviderTextDelta(text=text))
        blocks.append(
            ToolUseBlock(id=id or f"toolu_{next(_ids)}", name=name, input=dict(args or {}))
        )
        return cls(
            message=Message(role="assistant", content=blocks),
            stop_reason="tool_use",
            deltas=tuple(deltas),
        )

    @classmethod
    def fail(cls, error: BaseException) -> "Turn":
        return cls(error=error)

    @classmethod
    def hang(cls) -> "Turn":
        return cls(delay=3600)


class FakeProvider:
    """Replays scripted turns and records what it was sent."""

    name = "fake"

    def __init__(self, turns: list[Turn], *, repeat_last: bool = False) -> None:
        self.turns, self.repeat_last = list(turns), repeat_last
        self.requests: list[CompletionRequest] = []

    async def stream(self, req: CompletionRequest) -> AsyncIterator[ProviderEvent]:
        index = len(self.requests)
        self.requests.append(req)
        if index < len(self.turns):
            turn = self.turns[index]
        elif self.repeat_last and self.turns:
            turn = self.turns[-1]
        else:
            raise AssertionError(
                f"request {index + 1} arrived but only {len(self.turns)} turns were scripted"
            )
        if turn.delay:
            await asyncio.sleep(turn.delay)
        if turn.error is not None:
            raise turn.error
        for delta in turn.deltas:
            yield delta
        yield CompletionDone(message=turn.message, stop_reason=turn.stop_reason, usage=Usage())  # type: ignore[arg-type]


class FakeDocker:
    def __init__(self, logs: dict[str, list[str]] | None = None) -> None:
        self.logs = logs or {}
        self.calls: list[tuple[str, int]] = []

    def container_logs(self, container_id: str, tail: int):
        self.calls.append((container_id, tail))
        return self.logs.get(container_id)


def agent_factory(provider: FakeProvider, calls: list[dict] | None = None):
    """A `make_agent` that builds a real logpose Agent around a fake model.

    The base URL and API key are recorded (so tests can check what would have been sent)
    but not forwarded: a fake model needs neither.
    """

    def make(**kwargs):
        if calls is not None:
            calls.append(dict(kwargs))
        kwargs = {k: v for k, v in kwargs.items() if k not in ("base_url", "api_key")}
        return Agent(provider, **kwargs)

    return make


def save_config(settings: Settings, **over) -> None:
    args = {
        "base_url": "https://api.example.com/v1",
        "model": "test-model",
        "instructions": "",
        "api_key": API_KEY,
        "now": datetime(2026, 1, 1, tzinfo=UTC),
    }
    args.update(over)
    conn = connect(settings.db_path)
    try:
        save_ai(conn, CRYPTO_KEY, **args)
    finally:
        conn.close()


def make_operator(settings: Settings, provider: FakeProvider, docker=None, calls=None) -> Operator:
    return Operator(
        settings,
        docker or FakeDocker(),
        CRYPTO_KEY,
        datetime.now(UTC),
        make_agent=agent_factory(provider, calls),
    )


async def collect(
    operator: Operator, message: str, conversation_id: str | None = None
) -> list[dict]:
    return [event async for event in operator.chat(message, conversation_id)]

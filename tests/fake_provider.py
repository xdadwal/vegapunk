"""A scriptable logpose provider — the model, replaced by a script.

logpose owns the loop, so testing Vegapunk's half of it means controlling
exactly what the "model" says on each round trip. ``FakeProvider`` replays a
list of ``Turn`` values (one per provider call) and records every request it was
handed, so a test can assert on what was actually sent.

Nothing here touches the network, a credential, or a model.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from logpose import (
    Agent,
    CompletionDone,
    CompletionRequest,
    Message,
    ProviderEvent,
    ProviderTextDelta,
    ProviderThinkingDelta,
    StopReason,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    Usage,
)

_IDS = itertools.count(1)


def call(name: str, arguments: dict[str, Any] | None = None, *, id: str | None = None):
    """A tool call for a scripted turn."""
    return ToolUseBlock(
        id=id if id is not None else f"toolu_{next(_IDS)}", name=name, input=dict(arguments or {})
    )


@dataclass(frozen=True)
class Turn:
    """One provider round trip: what to stream, then how the turn ended.

    Attributes:
        text: The assistant's spoken text for this turn.
        thinking: Reasoning text, emitted before the reply.
        calls: Tool calls the model is requesting, in wire order.
        chunks: Fragments to stream ``text`` as; defaults to one chunk.
        stream_text: When False, ``text`` is in the final message but no delta
            is emitted — a provider that assembles rather than streams.
        stop_reason: Overridable to script a truncated turn ("max_tokens").
        usage: Token usage reported for this turn.
        error: Raised from the stream instead of completing.
        delay: Seconds to sleep before emitting anything.
    """

    text: str | None = None
    thinking: str | None = None
    calls: tuple[ToolUseBlock, ...] = ()
    chunks: tuple[str, ...] | None = None
    stream_text: bool = True
    stop_reason: StopReason | None = None
    usage: Usage = field(default_factory=Usage)
    error: BaseException | None = None
    delay: float = 0.0

    def _message(self) -> Message:
        blocks: list[Any] = []
        if self.thinking is not None:
            blocks.append(ThinkingBlock(thinking=self.thinking))
        if self.text is not None:
            blocks.append(TextBlock(text=self.text))
        blocks.extend(self.calls)
        return Message(role="assistant", content=blocks)

    def _deltas(self) -> list[ProviderEvent]:
        deltas: list[ProviderEvent] = []
        if self.thinking is not None:
            deltas.append(ProviderThinkingDelta(text=self.thinking))
        if self.text is not None and self.stream_text:
            for chunk in self.chunks if self.chunks is not None else (self.text,):
                if chunk:
                    deltas.append(ProviderTextDelta(text=chunk))
        return deltas

    def _stop(self) -> StopReason:
        if self.stop_reason is not None:
            return self.stop_reason
        return "tool_use" if self.calls else "end_turn"


def says(text: str, **kwargs: Any) -> Turn:
    """A turn where the model just answers."""
    return Turn(text=text, **kwargs)


def wants(*calls: ToolUseBlock, **kwargs: Any) -> Turn:
    """A turn where the model asks for tools."""
    return Turn(calls=calls, **kwargs)


class FakeProvider:
    """Replays scripted turns and records what it was sent."""

    name = "fake"
    model_default = "fake-model"

    def __init__(self, turns: Sequence[Turn] | Turn = (), *, repeat_last: bool = False) -> None:
        self.turns: list[Turn] = [turns] if isinstance(turns, Turn) else list(turns)
        self.repeat_last = repeat_last
        self.requests: list[CompletionRequest] = []
        self.closed = 0

    @property
    def call_count(self) -> int:
        return len(self.requests)

    @property
    def last_request(self) -> CompletionRequest:
        assert self.requests, "FakeProvider was never called"
        return self.requests[-1]

    def _turn_for(self, index: int) -> Turn:
        if index < len(self.turns):
            return self.turns[index]
        if self.repeat_last and self.turns:
            return self.turns[-1]
        raise AssertionError(
            f"FakeProvider ran out of scripted turns: request #{index + 1} arrived "
            f"but only {len(self.turns)} turn(s) were scripted."
        )

    async def stream(self, req: CompletionRequest) -> AsyncIterator[ProviderEvent]:
        turn = self._turn_for(len(self.requests))
        self.requests.append(req)
        try:
            if turn.delay:
                await asyncio.sleep(turn.delay)
            if turn.error is not None:
                raise turn.error
            for delta in turn._deltas():
                yield delta
            yield CompletionDone(
                message=turn._message(), stop_reason=turn._stop(), usage=turn.usage
            )
        except GeneratorExit:
            # Recorded so cancellation tests can prove the loop closed us
            # rather than leaking us.
            self.closed += 1
            raise


# --- building saved history, in the shape the store round-trips -------------


def user_turn(text: str) -> dict:
    """A turn the human took."""
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def assistant_turn(text: str) -> dict:
    """A turn the model spoke."""
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def tool_turns(name: str, result: str, *, id: str = "c1") -> list[dict]:
    """The two turns a tool call occupies: the request, then its result.

    The result rides on a ``user`` message — that's how the provider wants it —
    which is exactly why "role == user" can't be read as "the human spoke".
    """
    return [
        {"role": "assistant", "content": [{"type": "tool_use", "id": id, "name": name, "input": {}}]},
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": id, "content": result}],
        },
    ]


def conversation(n: int) -> list[dict]:
    """``n`` exchanges: q0/a0 … q{n-1}/a{n-1}."""
    turns: list[dict] = []
    for i in range(n):
        turns.extend([user_turn(f"q{i}"), assistant_turn(f"a{i}")])
    return turns


def backend_for(
    turns: Sequence[Turn] | Turn = (),
    *,
    model_label: str = "unknown-model",
    context_window: int = 0,
    effort_key: str = "",
    repeat_last: bool = False,
    title: str = "scripted title",
):
    """A Backend over a scripted model, for tests that drive a whole Session.

    ``title`` scripts the *titling* agent separately, because it runs on its own
    provider — two agents can't share one provider's event loop, so a session's
    conversation script and its auto-name script are genuinely independent.

    ``effort_key`` names the request field an effort level would ride in, which
    is also what makes ``supports_effort`` true: "output_config" for a Messages
    backend, "reasoning" for a Responses one, "" for a backend with no such
    setting. Naming the field rather than passing a bare flag is what stops a
    test from pinning a state the real code cannot produce.
    """
    from vegapunk.backend import Backend

    return Backend(
        provider=FakeProvider(turns, repeat_last=repeat_last),
        model_label=model_label,
        context_window=context_window,
        effort_key=effort_key,
        spawn_provider=lambda: FakeProvider(says(title), repeat_last=True),
    )


def session_for(turns: Sequence[Turn] | Turn = (), **kwargs: Any):
    """A Session over a scripted model — what the CLI and command tests drive.

    Everything a Session needs is defaulted, so a test that only cares about
    slash commands can build one with no arguments.
    """
    from vegapunk.session import Session

    tools = kwargs.pop("tools", [])
    backend_kwargs = {
        key: kwargs.pop(key)
        for key in ("model_label", "context_window", "effort_key", "repeat_last", "title")
        if key in kwargs
    }
    kwargs.setdefault("system_prompt", "SYS")
    return Session(backend_for(turns, **backend_kwargs), tools, **kwargs)


def agent_for(
    turns: Sequence[Turn] | Turn,
    *,
    tools: Sequence[Any] = (),
    approver: Any = None,
    max_iterations: int = 25,
    repeat_last: bool = False,
    system: str = "system",
) -> tuple[Agent, FakeProvider]:
    """An Agent wired exactly the way Vegapunk wires one, over a scripted model."""
    from vegapunk.gate import make_gate

    provider = FakeProvider(turns, repeat_last=repeat_last)
    agent = Agent(
        provider,
        system=system,
        tools=list(tools),
        max_iterations=max_iterations,
        on_tool_call=make_gate(approver),
    )
    return agent, provider

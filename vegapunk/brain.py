"""The Brain — Vegapunk's swappable model layer.

``Brain`` is the only place that knows how to talk to an LLM. The loop, the
tools, and the CLI depend on this small interface rather than on any specific
provider, so swapping models later is a one-class change.

Streaming grows the contract again: ``think`` is now a *generator*. It yields
``ReasoningDelta`` / ``TextDelta`` fragments the moment the server produces
them, then yields the assembled ``BrainResponse`` as its final event, so a
consumer can render tokens live while everything downstream still gets the
complete turn — translated into Vegapunk's own neutral types so the rest of
the app never touches OpenAI/DMR types directly. The stream is lazy: nothing
is sent to the server until the first pull, and a consumer that stops pulling
(``.close()``) tears the request down cleanly.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field

from openai import OpenAI

from .config import Config, config


@dataclass
class ToolCall:
    """A model's request to run one tool, in Vegapunk's own neutral shape."""

    id: str
    name: str
    arguments: dict


@dataclass
class ReasoningDelta:
    """A fragment of the model's chain-of-thought, yielded as it's generated.

    Display-only, like ``BrainResponse.reasoning`` — it never enters history.
    """

    text: str


@dataclass
class TextDelta:
    """A fragment of the assistant's reply text, yielded as it's generated."""

    text: str


@dataclass
class BrainResponse:
    """One complete turn from the model — always a ``think`` stream's last event."""

    # The assistant message, OpenAI-shaped, ready to append back to history.
    message: dict
    # The final text answer, if any (None when the model only wants tools).
    # Contract for every Brain: equals the joined TextDeltas the stream
    # yielded before it, so a live renderer and history never disagree.
    text: str | None
    # Tools the model asked to run this turn (empty when it's finished).
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The model's chain-of-thought for this turn, if the server returned one
    # (e.g. reasoning models' `reasoning_content`). Display-only — never
    # replayed into history. None when absent.
    reasoning: str | None = None
    # True when the server stopped generating early (finish_reason "length" —
    # out of tokens/context): the text is a cut-off prefix, not the model's
    # chosen ending. Display-only, like reasoning — the loop notes it on the
    # trace so a truncated reply is never passed off as complete.
    truncated: bool = False
    # The server's own count of tokens this request occupied (prompt +
    # completion — i.e. the whole conversation's current footprint in the
    # context window). None when the server didn't report usage.
    context_tokens: int | None = None


# Everything a think() stream can yield: deltas while the model generates,
# then exactly one BrainResponse, last.
ThinkEvent = ReasoningDelta | TextDelta | BrainResponse


def final_response(events: Iterator[ThinkEvent]) -> BrainResponse:
    """Drain a ``think`` stream and return its final ``BrainResponse``.

    For callers that want the assembled turn and don't care about watching it
    arrive (session titling, one-shot scripts, tests).
    """
    response = None
    for event in events:
        if isinstance(event, BrainResponse):
            response = event
    if response is None:
        raise RuntimeError("think() stream ended without a final BrainResponse")
    return response


class Brain(ABC):
    """Given the conversation so far (and any tools it may use), stream a turn:
    zero or more deltas, then the complete ``BrainResponse``, last."""

    @abstractmethod
    def think(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[ThinkEvent]:
        ...


class DMRBrain(Brain):
    """A Brain backed by an OpenAI-compatible server (Docker Model Runner)."""

    def __init__(self, cfg: Config = config) -> None:
        self._model = cfg.model
        self._client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)

    def think(self, messages: list[dict], tools: list[dict] | None = None) -> Iterator[ThinkEvent]:
        kwargs: dict = {
            "model": self._model,
            "messages": messages,
            "stream": True,
            # Ask for the usage rider — a trailing choices-less chunk carrying
            # exact token counts, which is how the toolbar knows how full the
            # context window is.
            "stream_options": {"include_usage": True},
        }
        if tools:
            kwargs["tools"] = tools

        content: list[str] = []
        reasoning: list[str] = []
        # Tool calls stream as fragments keyed by index: id/name arrive once,
        # but a call's argument JSON may be split across many chunks — collect
        # everything, assemble only after the stream ends.
        calls: dict[int, dict] = {}

        finish_reason: str | None = None
        usage = None

        # `with` so an abandoned stream (consumer closes us mid-turn, Ctrl-C)
        # releases the HTTP connection instead of leaving it dangling.
        with self._client.chat.completions.create(**kwargs) as stream:
            for chunk in stream:
                # The usage rider arrives on a trailing chunk with no choices,
                # so it must be read before the empty-choices skip.
                usage = getattr(chunk, "usage", None) or usage
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                finish_reason = choice.finish_reason or finish_reason
                delta = choice.delta
                # Reasoning models stream their chain-of-thought in a separate
                # field; the SDK delta allows extra fields, so read defensively.
                thought = getattr(delta, "reasoning_content", None)
                if thought:
                    reasoning.append(thought)
                    yield ReasoningDelta(thought)
                if delta.content:
                    content.append(delta.content)
                    yield TextDelta(delta.content)
                for tc in delta.tool_calls or []:
                    slot = calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                    slot["id"] = tc.id or slot["id"]
                    if tc.function:
                        slot["name"] = tc.function.name or slot["name"]
                        slot["arguments"] += tc.function.arguments or ""

        if finish_reason is None:
            # The server never said the turn finished — it disconnected
            # mid-generation. A half-answer passed off as complete would be
            # displayed, replayed into history, and saved to disk; fail
            # loudly instead.
            raise RuntimeError(
                "Model stream ended without a finish_reason — the server "
                "disconnected mid-turn, so the partial answer was discarded."
            )

        yield self._assemble(
            text="".join(content) if content else None,
            reasoning="".join(reasoning),
            raw_calls=[calls[i] for i in sorted(calls)],
            truncated=finish_reason == "length",
            context_tokens=getattr(usage, "total_tokens", None),
        )

    @staticmethod
    def _assemble(
        text: str | None,
        reasoning: str,
        raw_calls: list[dict],
        truncated: bool,
        context_tokens: int | None,
    ) -> BrainResponse:
        """Build the finished turn from what the stream accumulated."""
        tool_calls: list[ToolCall] = []
        for rc in raw_calls:
            try:
                arguments = json.loads(rc["arguments"] or "{}")
            except json.JSONDecodeError:
                # A small model can emit malformed argument JSON; don't crash
                # the loop — pass empty args and let the tool/model recover.
                arguments = {}
            tool_calls.append(ToolCall(id=rc["id"], name=rc["name"], arguments=arguments))

        # Rebuild a clean assistant turn to replay into history, carrying only
        # the fields the server expects back — reasoning is deliberately
        # omitted (servers reject/ignore it and it just bloats context; the
        # model regenerates fresh reasoning each turn).
        assistant_message: dict = {"role": "assistant", "content": text}
        if raw_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": rc["id"],
                    "type": "function",
                    "function": {"name": rc["name"], "arguments": rc["arguments"]},
                }
                for rc in raw_calls
            ]

        return BrainResponse(
            message=assistant_message,
            text=text,
            tool_calls=tool_calls,
            reasoning=reasoning.strip() or None,
            truncated=truncated,
            context_tokens=context_tokens,
        )

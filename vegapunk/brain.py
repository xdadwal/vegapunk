"""The Brain — Vegapunk's swappable model layer.

``Brain`` is the only place that knows how to talk to an LLM. The loop, the
tools, and the CLI depend on this small interface rather than on any specific
provider, so swapping models later is a one-class change.

Step 3 grows the contract: ``think`` now also accepts tool schemas and reports
back any tool calls the model wants to make — translated into Vegapunk's own
neutral types (``ToolCall``/``BrainResponse``) so the rest of the app never
touches OpenAI/DMR types directly.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
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
class BrainResponse:
    """One turn from the model."""

    # The assistant message, OpenAI-shaped, ready to append back to history.
    message: dict
    # The final text answer, if any (None when the model only wants tools).
    text: str | None
    # Tools the model asked to run this turn (empty when it's finished).
    tool_calls: list[ToolCall] = field(default_factory=list)
    # The model's chain-of-thought for this turn, if the server returned one
    # (e.g. reasoning models' `reasoning_content`). Display-only — never
    # replayed into history. None when absent.
    reasoning: str | None = None


class Brain(ABC):
    """Given the conversation so far (and any tools it may use), return a turn."""

    @abstractmethod
    def think(self, messages: list[dict], tools: list[dict] | None = None) -> BrainResponse:
        ...


class DMRBrain(Brain):
    """A Brain backed by an OpenAI-compatible server (Docker Model Runner)."""

    def __init__(self, cfg: Config = config) -> None:
        self._model = cfg.model
        self._client = OpenAI(base_url=cfg.base_url, api_key=cfg.api_key)

    def think(self, messages: list[dict], tools: list[dict] | None = None) -> BrainResponse:
        kwargs: dict = {"model": self._model, "messages": messages}
        if tools:
            kwargs["tools"] = tools

        message = self._client.chat.completions.create(**kwargs).choices[0].message

        # Reasoning models put their chain-of-thought in a separate field; the
        # OpenAI SDK message allows extra fields, so read it defensively and
        # normalize empty -> None. Display-only — see below: it's kept out of
        # the replayed history turn.
        reasoning = getattr(message, "reasoning_content", None)
        if isinstance(reasoning, str):
            reasoning = reasoning.strip() or None

        tool_calls: list[ToolCall] = []
        for tc in message.tool_calls or []:
            try:
                arguments = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                # A small model can emit malformed argument JSON; don't crash
                # the loop — pass empty args and let the tool/model recover.
                arguments = {}
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, arguments=arguments))

        # Rebuild a clean assistant turn to replay into history. We reconstruct
        # it (rather than dumping the SDK object) so only the fields the server
        # expects get sent back on the next request — `reasoning_content` is
        # deliberately omitted (servers reject/ignore it and it just bloats
        # context; the model regenerates fresh reasoning each turn).
        assistant_message: dict = {"role": "assistant", "content": message.content}
        if message.tool_calls:
            assistant_message["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in message.tool_calls
            ]

        return BrainResponse(
            message=assistant_message,
            text=message.content,
            tool_calls=tool_calls,
            reasoning=reasoning,
        )

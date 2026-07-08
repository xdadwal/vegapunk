"""A Session — a running conversation with Vegapunk.

Owns the message history so a conversation persists across turns (unlike the
one-shot ``run()``), seeding the system prompt exactly once. The CLI, the tests,
and any future interface all drive Vegapunk through this.
"""

from __future__ import annotations

from collections.abc import Generator

from .approval import Approver
from .brain import Brain, TextDelta, final_response
from .config import config
from .loop import drive_turns
from .tools import Tool


class Session:
    def __init__(
        self,
        brain: Brain,
        tools: list[Tool],
        system_prompt: str = config.system_prompt,
        max_steps: int = config.max_steps,
        approver: Approver | None = None,
    ) -> None:
        self._brain = brain
        self._schemas = [tool.to_schema() for tool in tools]
        self._by_name = {tool.name: tool for tool in tools}
        self._max_steps = max_steps
        # Guards side-effecting tools. None means no gate (used by tests).
        self._approver = approver
        # The conversation's current footprint in the model's context window
        # (server-reported tokens, from the latest completed turn). None until
        # the first turn — and again after reset/restore, when any old number
        # would describe a conversation the model hasn't seen yet.
        self.context_tokens: int | None = None
        # The system prompt is seeded once, here — never re-added per turn.
        self._messages: list[dict] = [{"role": "system", "content": system_prompt}]

    def send(self, user_input: str) -> Generator[TextDelta, None, str]:
        """Add a user turn, run the agent loop, and stream Vegapunk's reply.

        A generator: yields ``TextDelta`` fragments as the model produces them
        and *returns* the complete reply via ``StopIteration.value``. Lazy,
        like all generators — nothing (not even the history append) happens
        until the first pull, so a created-but-never-consumed send is a no-op.
        """
        checkpoint = len(self._messages)
        self._messages.append({"role": "user", "content": user_input})
        try:
            reply, context_tokens = yield from drive_turns(
                self._brain,
                self._by_name,
                self._schemas,
                self._messages,
                self._max_steps,
                self._approver,
            )
            if context_tokens is not None:
                self.context_tokens = context_tokens
            return reply
        except BaseException:
            # Interrupted (Ctrl-C inside a pull), abandoned (``.close()``
            # throws GeneratorExit in at the paused yield), or the turn failed
            # outright (a brain/network error): whatever ended the turn early,
            # roll the partial turn back out so history — and the autosave —
            # never carry a half-turn, then re-raise for the caller.
            del self._messages[checkpoint:]
            raise

    @property
    def brain(self) -> Brain:
        """The live model backend (for the toolbar and /model)."""
        return self._brain

    def swap_brain(self, brain: Brain) -> None:
        """Switch the model mid-conversation.

        History is OpenAI-shaped dicts — portable across brains — so the
        conversation simply continues on the new model. The token footprint is
        cleared: the old number describes the old model's context, and the new
        one reports its own on the next turn.
        """
        self._brain = brain
        self.context_tokens = None

    def reset(self) -> None:
        """Clear the conversation but keep the seeded system prompt.

        The approver is left untouched, so any "always allow" trust granted this
        session survives a reset — reset clears the conversation, not your
        approval decisions.
        """
        del self._messages[1:]
        self.context_tokens = None  # a fresh conversation has no footprint yet

    def restore(self, messages: list[dict]) -> None:
        """Replace the conversation with a saved one (resume).

        A faithful restore — keeps the saved system turn as-is, so resuming a
        session reproduces exactly what the model last saw.
        """
        self._messages = list(messages)
        # Unknown until the next turn reports it — saved sessions don't carry
        # token counts, and a stale number would describe the old conversation.
        self.context_tokens = None

    def suggest_name(self) -> str:
        """Ask the model for a short title for this conversation, from its first
        user message — used to auto-name a session.

        Best-effort and isolated: it runs on a throwaway message list (never
        touching history) and returns ``""`` if there's no user turn yet or the
        call fails, so the caller can fall back to a slug of the message text. A
        failed title must never break the actual turn.
        """
        first = next(
            (m["content"] for m in self._messages if m.get("role") == "user" and m.get("content")),
            None,
        )
        if not first:
            return ""
        probe = [
            {
                "role": "system",
                "content": "Reply with a short 3-5 word title for a conversation that begins "
                "with the next message. Give the title only — no quotes, no punctuation.",
            },
            {"role": "user", "content": first},
        ]
        try:
            # Drain the think stream — a title isn't worth rendering live.
            return (final_response(self._brain.think(probe)).text or "").strip()
        except Exception:  # noqa: BLE001 — titling is optional; fall back, never crash the turn
            return ""

    @property
    def messages(self) -> list[dict]:
        """A snapshot of the message history (for inspection and tests).

        A copy, so callers can't mutate the session's internal state.
        """
        return list(self._messages)

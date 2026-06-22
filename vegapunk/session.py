"""A Session — a running conversation with Vegapunk.

Owns the message history so a conversation persists across turns (unlike the
one-shot ``run()``), seeding the system prompt exactly once. The CLI, the tests,
and any future interface all drive Vegapunk through this.
"""

from __future__ import annotations

from .approval import Approver
from .brain import Brain
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
        # The system prompt is seeded once, here — never re-added per turn.
        self._messages: list[dict] = [{"role": "system", "content": system_prompt}]

    def send(self, user_input: str) -> str:
        """Add a user turn, run the agent loop, and return Vegapunk's reply."""
        checkpoint = len(self._messages)
        self._messages.append({"role": "user", "content": user_input})
        try:
            return drive_turns(
                self._brain,
                self._by_name,
                self._schemas,
                self._messages,
                self._max_steps,
                self._approver,
            )
        except KeyboardInterrupt:
            # Interrupted mid-generation: roll the partial turn back out so the
            # history stays consistent, then let the caller decide what to do.
            del self._messages[checkpoint:]
            raise

    def reset(self) -> None:
        """Clear the conversation but keep the seeded system prompt.

        The approver is left untouched, so any "always allow" trust granted this
        session survives a reset — reset clears the conversation, not your
        approval decisions.
        """
        del self._messages[1:]

    @property
    def messages(self) -> list[dict]:
        """A snapshot of the message history (for inspection and tests).

        A copy, so callers can't mutate the session's internal state.
        """
        return list(self._messages)

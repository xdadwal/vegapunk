"""A Session — a running conversation with Vegapunk.

Owns the history so a conversation persists across turns (unlike the one-shot
``run()``), and owns the logpose ``Agent`` that carries the model, the tools, the
step limit, and the approval gate. The CLI, the tests, and any future interface
all drive Vegapunk through this.

The system prompt is the agent's, not a message: it's sent with every turn but
never appears in the history, so it can't be edited by a restore or counted as a
turn.
"""

from __future__ import annotations

import sys
from collections.abc import Generator

from logpose import (
    Agent,
    Conversation,
    Message,
    RawBlock,
    RedactedThinkingBlock,
    TextDelta,
    ThinkingBlock,
    close_sync,
    run_sync,
    stream_sync,
)

from . import render, style
from .approval import Approver
from .backend import Backend, with_effort
from .config import config
from .gate import make_gate
from .loop import trace
from .render import Renderer

_TITLE_PROMPT = (
    "Reply with a short 3-5 word title for a conversation that begins "
    "with the next message. Give the title only — no quotes, no punctuation."
)


def _portable(messages: list[Message]) -> list[Message]:
    """The same history with every backend-specific block removed.

    Reasoning is the part of the history that does *not* travel between
    models, and each backend encodes it differently — so both encodings go.

    ``RawBlock`` is dropped for the same reason, and it is the one that bites
    later rather than immediately: the Responses backends (``codex``,
    ``openai``) round-trip reasoning as a ``RawBlock`` carrying an opaque
    ``encrypted_content`` blob, never as a ``ThinkingBlock``. A conversation
    that visited Codex and then switched to Claude therefore carried blocks
    Anthropic never signed and cannot parse, and failed on *every* later turn
    until /new — while a fresh conversation on the same model worked, which is
    what makes it read as a Claude problem rather than a swap problem.

    Dropping ``RawBlock`` costs a claude->claude swap the unmodelled Anthropic
    blocks it preserves (server tool use, search results). That is the right
    trade here: those matter for re-sending a *paused* turn within one backend,
    and this function only runs when the conversation is leaving the backend
    that produced them. Anthropic signs its thinking blocks and rejects a turn whose blocks
    were altered — but it equally rejects one carrying a block it never signed
    (``messages.N.content.0.thinking.signature: Field required``), which is
    exactly what a local reasoning model's thinking looks like. So a
    conversation that starts on a thinking model and then moves to Claude fails
    on *every* later turn, permanently, until /new.

    Dropping the blocks at the moment of the swap is what keeps the
    conversation portable: they're display-only here (already traced to
    stderr), and the new model has no use for another model's reasoning. An
    assistant turn left with no content at all is dropped whole — an empty
    content array is itself a 400.
    """
    kept: list[Message] = []
    for message in messages:
        content = [
            block
            for block in message.content
            if not isinstance(block, (ThinkingBlock, RedactedThinkingBlock, RawBlock))
        ]
        if len(content) == len(message.content):
            kept.append(message)
        elif content:
            kept.append(message.model_copy(update={"content": content}))
    return kept


class Session:
    def __init__(
        self,
        backend: Backend,
        tools: list,
        system_prompt: str = config.system_prompt,
        max_steps: int = config.max_steps,
        approver: Approver | None = None,
        renderer: Renderer | None = None,
    ) -> None:
        self._backend = backend
        self._tools = tools
        self._system_prompt = system_prompt
        self._max_steps = max_steps
        # Guards side-effecting tools. None means no gate — fail-closed, so a
        # guarded tool is blocked rather than run unattended.
        self._approver = approver
        self._agent = self._build_agent()
        self._titler: Agent | None = None
        # One renderer for both channels of a turn: the trace writes through it
        # and so does the reply, so they can coordinate when one of them needs
        # to own the cursor.
        self._renderer = renderer or render.pick()
        # The conversation's current footprint in the model's context window
        # (server-reported tokens, from the latest completed turn). None until
        # the first turn — and again after reset/restore, when any old number
        # would describe a conversation the model hasn't seen yet.
        self.context_tokens: int | None = None
        self._last_reasoning = ""
        self._conversation = Conversation()

    def _build_agent(self) -> Agent:
        return Agent(
            self._backend.provider,
            system=self._system_prompt,
            tools=self._tools,
            max_iterations=self._max_steps,
            extra=self._backend.extra,
            on_tool_call=make_gate(self._approver),
        )

    def send(self, user_input: str) -> Generator[TextDelta, None, str]:
        """Add a user turn, run the agent loop, and stream Vegapunk's reply.

        A generator: yields ``TextDelta`` fragments as the model produces them
        and *returns* the complete reply via ``StopIteration.value``. Lazy,
        like all generators — nothing (not even the history append) happens
        until the first pull, so a created-but-never-consumed send is a no-op.
        """
        checkpoint = len(self._conversation.messages)
        try:
            # The run is started here, beside the conversation it appends to and
            # the rollback below that undoes those appends. ``trace`` renders the
            # stream and closes it; it never sees the agent or the history.
            reply, context_tokens = yield from trace(
                stream_sync(self._agent, user_input, conversation=self._conversation),
                self._renderer,
            )
            if context_tokens is not None:
                self.context_tokens = context_tokens
            self._last_reasoning = self._renderer.last_reasoning
            return reply
        except BaseException:
            # Interrupted (Ctrl-C inside a pull), abandoned (``.close()``
            # throws GeneratorExit in at the paused yield), or the turn failed
            # outright (a provider/network error): whatever ended the turn
            # early, roll the partial turn back out so history — and the
            # autosave — never carry a half-turn, then re-raise for the caller.
            #
            # A *new* Conversation rather than truncating this one in place: a
            # real SIGINT unblocks us without cancelling the coroutine still
            # running on logpose's loop thread, and that coroutine appends the
            # assistant turn before we ever see the interrupt. Truncating would
            # let a late append land back on the list we just cleaned, leaving
            # an assistant-first history that the next request rejects. Handing
            # the loop an orphaned list is what makes the rollback final.
            self._conversation = Conversation(self._conversation.messages[:checkpoint])
            raise

    @property
    def renderer(self) -> Renderer:
        """The renderer this session's turns print through."""
        return self._renderer

    @property
    def last_reasoning(self) -> str:
        """Display-only reasoning from the last completed turn."""
        return self._last_reasoning

    @property
    def model_label(self) -> str:
        """The live model's name (for the toolbar and /model)."""
        return self._backend.model_label

    @property
    def context_window(self) -> int:
        """The live model's context window, for the toolbar's fullness gauge."""
        return self._backend.context_window

    @property
    def backend(self) -> Backend:
        """The live backend (for /effort, which asks what it supports)."""
        return self._backend

    def swap_backend(self, backend: Backend) -> None:
        """Switch the model mid-conversation.

        History is provider-neutral, so the conversation simply continues on the
        new model. The token footprint is cleared: the old number describes the
        old model's context, and the new one reports its own on the next turn.

        Keeping the *same* provider (what /effort does) must not rebuild the
        agent: each agent owns a private event-loop thread, and the provider's
        HTTP client is bound to whichever loop first touched it. Building a
        second agent and closing the first would leave the client pointing at a
        closed loop, and every later turn would fail with "Event loop is
        closed". Since ``extra`` is re-read per request, updating it in place is
        both sufficient and the only safe move.
        """
        self._backend = backend
        if backend.provider is self._agent.provider:
            self._agent.extra = dict(backend.extra)
        else:
            # A real model change: another model's thinking cannot be replayed
            # to this one, so it doesn't travel. ``send`` hands the conversation
            # to the agent per turn, so swapping it here is enough.
            self._conversation = Conversation(_portable(self._conversation.messages))
            old = self._agent
            self._agent = self._build_agent()
            self._retire(old)
            # The titler belongs to the old provider; drop it so the next
            # autosave builds one on the new model.
            self._retire_titler()
        self.context_tokens = None

    def set_effort(self, level: str) -> None:
        """Change the model's effort level (the /effort command).

        Raises ``ValueError`` on a bad level, or on a backend that has no such
        setting. Rebuilds the agent — effort rides on every request — but keeps
        the same provider, so the connection underneath survives.
        """
        self.swap_backend(with_effort(self._backend, level))

    def reset(self) -> None:
        """Clear the conversation.

        The approver is left untouched, so any "always allow" trust granted this
        session survives a reset — reset clears the conversation, not your
        approval decisions.
        """
        self._conversation.clear()
        self.context_tokens = None  # a fresh conversation has no footprint yet
        self._last_reasoning = ""

    def restore(self, messages: list[dict]) -> None:
        """Replace the conversation with a saved one (resume).

        Raises ``ValueError`` if a message doesn't parse — the caller has
        already checked the format, so this is the backstop, not the gate.
        """
        # Thinking is stripped for the same reason as in swap_backend: a saved
        # conversation carries no record of which model produced it, so its
        # thinking blocks cannot be assumed replayable to whatever is live now.
        self._conversation = Conversation(
            _portable([Message.model_validate(m) for m in messages])
        )
        # Unknown until the next turn reports it — saved sessions don't carry
        # token counts, and a stale number would describe the old conversation.
        self.context_tokens = None
        self._last_reasoning = ""

    def suggest_name(self) -> str:
        """Ask the model for a short title for this conversation, from its first
        user message — used to auto-name a session.

        Best-effort and isolated: it runs on a throwaway agent with no tools and
        its own prompt (never touching this conversation), and returns ``""`` if
        there's no user turn yet or the call fails, so the caller can fall back
        to a slug of the message text. A failed title must never break the turn.
        """
        first = next((m.text for m in self._conversation.messages if m.role == "user"), "")
        if not first:
            return ""
        try:
            if self._titler is None:
                # Its *own* provider, not this session's: each agent runs on a
                # private event-loop thread, and a provider's HTTP client binds
                # to whichever loop touches it first. Sharing one would make
                # every titling call fail with "bound to a different event
                # loop" — silently, since the fallback below swallows it.
                self._titler = Agent(
                    self._backend.spawn_provider(),
                    system=_TITLE_PROMPT,
                    extra=self._backend.extra,
                )
            return run_sync(self._titler, first).text.strip()
        except Exception as exc:  # noqa: BLE001 — titling is optional; never crash the turn
            # Said out loud on the watch channel: a title that silently never
            # works looks identical to a model that just picks bad titles.
            print(
                style.paint(f"  [session] could not title: {exc}", style.DIM, sys.stderr),
                file=sys.stderr,
            )
            return ""

    @property
    def messages(self) -> list[dict]:
        """A snapshot of the message history, ready to persist.

        Plain dicts, so callers can't mutate the session's internal state and
        the session store has nothing to convert.
        """
        return [message.model_dump(mode="json") for message in self._conversation.messages]

    def close(self) -> None:
        """Release the private event-loop threads this session's agents hold."""
        self._retire(self._agent)
        self._retire_titler()

    def _retire_titler(self) -> None:
        """Shut the titling agent down, if one was ever built."""
        if self._titler is not None:
            self._retire(self._titler)
            self._titler = None

    @staticmethod
    def _retire(agent: Agent) -> None:
        """Shut an agent down without letting cleanup take the session with it."""
        try:
            close_sync(agent)
        except Exception:  # noqa: BLE001 — teardown must not mask the real work
            pass

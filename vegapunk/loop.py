"""Watching the agent work — the loop's live trace.

logpose owns the think -> act -> observe cycle now. What lives here is the part
that was never really about looping: turning its event stream into something a
human can watch while it happens.

Two channels, deliberately separate. Reply text is re-yielded upward so the CLI
can render it token by token on stdout. Everything else — which step we're on,
what the model is chewing over, which tool ran and what came back — is traced to
stderr, so you can watch the loop work without that noise ever contaminating the
answer or the conversation history.

``trace`` is therefore a generator: it yields ``TextDelta`` fragments as they
arrive, and *returns* — via ``StopIteration.value`` — the final reply together
with the conversation's context footprint in tokens.

Nothing here knows about ``Agent``. ``trace`` takes the event stream and nothing
else, so the module that owns a conversation is the one that starts a run on it,
and this one is a pure function of what came back — a rendering contract can be
tested by handing it a few event objects, with no agent anywhere in sight.
"""

from __future__ import annotations

import itertools
import sys
import threading
import time
from collections.abc import Generator

from logpose import (
    Agent,
    Conversation,
    Event,
    MaxIterationsError,
    RunEnd,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolResult,
    TurnEnd,
    stream_sync,
)

from . import render
from .render import Renderer

# What the model is told when it runs out of steps. Yielded before it's returned
# (see the display invariant below), so a renderer never has to decide whether
# the return value still needs printing.
STEP_LIMIT_NOTICE = "(Stopped after hitting the step limit without a final answer.)"


def run(agent: Agent, user_input: str, renderer: Renderer | None = None) -> str:
    """One-shot: run a single request to completion and return the reply.

    Drains the turn stream internally (no live rendering by the caller), so
    script callers keep the simple call-and-get-a-string contract.
    """
    turns = trace(
        stream_sync(agent, user_input, conversation=Conversation()),
        renderer or render.pick(),
    )
    while True:
        try:
            next(turns)
        except StopIteration as stop:
            reply, _context_tokens = stop.value  # one-shots don't track fullness
            return reply


def trace(
    events: Generator[Event, None, None], renderer: Renderer
) -> Generator[TextDelta, None, tuple[str, int | None]]:
    """Render one run's event stream, and stream the reply back up.

    Yields ``TextDelta`` fragments of the assistant's speech as the model
    produces them, and *returns* — via ``StopIteration.value`` — the final text
    answer (or a notice if the step limit is hit) together with the
    conversation's context footprint in tokens: the server-reported total from
    the turn's last model call, None if the server never said.

    Takes ownership of ``events``: it is closed on every exit path, which is
    what cancels in-flight tools and tears down the provider connection when a
    caller walks away mid-turn. Whoever started the run appends to their own
    ``Conversation`` as it goes, so an interrupted turn leaves the partial
    history in place for them to roll back.

    Display invariant: any reply text is always yielded as deltas *before* being
    returned — a provider that didn't stream its answer and the step-limit notice
    are each synthesized into one delta — so a renderer can print exactly what it
    receives. Reasoning is not re-yielded; the renderer traces it live (today,
    to stderr beside [think]/[tool] — a future renderer needn't use stderr at
    all), and it never reaches stdout. It *does* stay in history,
    unlike under the old hand-rolled brains — the Anthropic API rejects a later
    turn whose thinking blocks were altered, so the assistant turn is stored and
    replayed verbatim.
    """
    context_tokens: int | None = None
    reply = ""
    step = 0
    turn_open = False
    spoke_this_turn = False
    # A turn's tool calls and results arrive *after* its TurnEnd, so they belong
    # to the step that just ended rather than opening a new one — and the wait
    # inside that phase is a tool running (or a human being asked to approve
    # it), which must not be sat on top of by a spinner.
    in_tool_phase = False
    # Tool arguments arrive on the ToolCall event and are printed beside the
    # result, which arrives later — hold them by id in between.
    pending_args: dict[str, dict] = {}

    spinner = _Spinner()
    try:
        stream = iter(events)
        while True:
            # Spin only while waiting on the model — the one stretch of true
            # silence a step has.
            if not turn_open and not in_tool_phase:
                spinner.set_status(_spinner_status(step + 1, context_tokens))
                spinner.start()
            try:
                event = next(stream)
            except StopIteration:
                break
            except MaxIterationsError:
                # The runaway backstop. Say so on both channels: the model's
                # answer is missing, and pretending otherwise would be a lie.
                spinner.stop()
                renderer.reasoning_end()
                yield TextDelta(STEP_LIMIT_NOTICE)
                return STEP_LIMIT_NOTICE, context_tokens
            spinner.stop()

            if not turn_open and isinstance(event, (ThinkingDelta, TextDelta, TurnEnd)):
                turn_open = True
                in_tool_phase = False
                spoke_this_turn = False
                step += 1
                renderer.step(step)

            if isinstance(event, ThinkingDelta):
                renderer.reasoning(event.text)
            elif isinstance(event, TextDelta):
                renderer.reasoning_end()
                if event.text:
                    spoke_this_turn = True
                    yield event
            elif isinstance(event, ToolCall):
                in_tool_phase = True
                if spoke_this_turn:
                    # The model spoke *and* called tools: tell the renderer to
                    # close the spoken line, rather than faking a "\n" delta as
                    # if the model had said it — see reply_break's docstring.
                    spoke_this_turn = False
                    renderer.reply_break()
                pending_args[event.id] = event.input
                # Announced before the wait, not after it: what follows is the
                # tool running — or a human at an approval prompt — and a
                # renderer holding part of the screen has to release it first.
                renderer.tool_call(event.name, event.input)
            elif isinstance(event, ToolResult):
                arguments = pending_args.pop(event.id, {})
                renderer.tool_result(event.name, arguments, event.content, event.is_error)
                # The next wait is the model's, so the spinner may resume.
                in_tool_phase = False
            elif isinstance(event, TurnEnd):
                turn_open = False
                renderer.reasoning_end()
                footprint = _footprint(event.usage)
                # Each turn sees the whole conversation, so the latest report is
                # the current footprint; keep the previous one if a server omits
                # usage entirely.
                if footprint:
                    context_tokens = footprint
                if event.stop_reason == "max_tokens":
                    # Out of tokens mid-answer: say so on the watch channel
                    # rather than passing a silently amputated reply off as the
                    # model's chosen ending.
                    renderer.note("the model ran out of tokens; this turn is cut off")
            elif isinstance(event, RunEnd):
                reply = event.result.text
                if reply and not spoke_this_turn:
                    # A provider that didn't stream its text still gets its
                    # answer displayed — see the display invariant above.
                    yield TextDelta(reply)
    finally:
        spinner.stop()
        renderer.reasoning_end()
        events.close()

    return reply, context_tokens


def _footprint(usage) -> int:
    """The conversation's size in tokens, as the next request would see it.

    Everything the model read this turn plus everything it wrote — the cached
    parts included, since they still occupy the window.
    """
    return (
        usage.input_tokens
        + usage.cache_read_input_tokens
        + usage.cache_creation_input_tokens
        + usage.output_tokens
    )


def _spinner_status(step: int, context_tokens: int | None) -> str:
    """The live wait label: stable facts from the last completed model step."""
    context = f" · {context_tokens:,} tok" if context_tokens is not None else ""
    return f"thinking… · step {step}{context}"


class _Spinner:
    """A '⠋ thinking…' line animated on stderr while the model hasn't produced
    its first event of a step.

    Interactive-terminal sugar only — gated on stderr being a TTY, not on the
    color setting (NO_COLOR means no color, not no animation; a piped trace
    never spins). A daemon thread owns the drawing so the main thread can stay
    blocked in the model read; ``stop()`` is idempotent, joins the thread, and
    erases the spinner's own line, so whatever prints next starts clean.
    """

    _FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status = "thinking…"
        self._started_at = 0.0

    def set_status(self, status: str) -> None:
        """Update the next wait's label before its drawing thread starts."""
        self._status = status

    def start(self) -> None:
        if not sys.stderr.isatty() or self._thread is not None:
            return
        self._stop.clear()
        self._started_at = time.monotonic()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self) -> None:
        for frame in itertools.cycle(self._FRAMES):
            # Draw before waiting, so even an instant stop() has one frame to
            # erase — which also makes the behavior deterministic to test.
            elapsed = int(time.monotonic() - self._started_at)
            print(
                f"\r  {frame} {self._status} · {elapsed}s",
                end="",
                file=sys.stderr,
                flush=True,
            )
            if self._stop.wait(0.1):
                return

    def stop(self) -> None:
        if self._thread is None:
            return  # never started (non-TTY) or already stopped
        self._stop.set()
        try:
            self._thread.join()
        finally:
            # The erase must survive a second Ctrl-C landing inside join()
            # (the classic double-mash) — without this finally, the mashed
            # interrupt would strand a stale "thinking…" frame on screen.
            self._thread = None
            # \r plus erase-to-end-of-line clears only the spinner's own
            # line; the [think] line above it is untouched.
            print("\r\x1b[K", end="", file=sys.stderr, flush=True)

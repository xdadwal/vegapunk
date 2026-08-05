"""Watching the agent work — the loop's live trace.

logpose owns the think -> act -> observe cycle now. What lives here is the part
that was never really about looping: turning its event stream into something a
human can watch while it happens.

Two channels, deliberately separate. Reply text is re-yielded upward so the CLI
can render it token by token on stdout. Everything else — which step we're on,
what the model is chewing over, which tool ran and what came back — is traced to
stderr, so you can watch the loop work without that noise ever contaminating the
answer or the conversation history.

``drive_turns`` is therefore a generator: it yields ``TextDelta`` fragments as
they arrive, and *returns* — via ``StopIteration.value`` — the final reply
together with the conversation's context footprint in tokens.
"""

from __future__ import annotations

import itertools
import sys
import threading
from collections.abc import Generator, Iterator

from logpose import (
    Agent,
    Conversation,
    MaxIterationsError,
    RunEnd,
    TextDelta,
    ThinkingDelta,
    ToolCall,
    ToolResult,
    TurnEnd,
    stream_sync,
)

from . import style

# What the model is told when it runs out of steps. Yielded before it's returned
# (see the display invariant below), so a renderer never has to decide whether
# the return value still needs printing.
STEP_LIMIT_NOTICE = "(Stopped after hitting the step limit without a final answer.)"


def run(agent: Agent, user_input: str) -> str:
    """One-shot: run a single request to completion and return the reply.

    Drains the turn stream internally (no live rendering), so script callers
    keep the simple call-and-get-a-string contract.
    """
    turns = drive_turns(agent, user_input, Conversation())
    while True:
        try:
            next(turns)
        except StopIteration as stop:
            reply, _context_tokens = stop.value  # one-shots don't track fullness
            return reply


def drive_turns(
    agent: Agent, user_input: str, conversation: Conversation
) -> Generator[TextDelta, None, tuple[str, int | None]]:
    """Run one request through ``agent``, tracing it, and stream the reply.

    Yields ``TextDelta`` fragments of the assistant's speech as the model
    produces them, and *returns* — via ``StopIteration.value`` — the final text
    answer (or a notice if the step limit is hit) together with the
    conversation's context footprint in tokens: the server-reported total from
    the turn's last model call, None if the server never said. Appends to
    ``conversation`` as it goes, so an interrupted turn leaves the partial
    history in place for the caller to roll back.

    Display invariant: any reply text is always yielded as deltas *before* being
    returned — a provider that didn't stream its answer and the step-limit notice
    are each synthesized into one delta — so a renderer can print exactly what it
    receives. Reasoning is not re-yielded; it's traced live to stderr here,
    beside [think]/[tool], and never reaches stdout. It *does* stay in history,
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

    events = stream_sync(agent, user_input, conversation=conversation)
    reasoning = _ReasoningLine()
    spinner = _Spinner()
    try:
        stream: Iterator = iter(events)
        while True:
            # Spin only while waiting on the model — the one stretch of true
            # silence a step has.
            if not turn_open and not in_tool_phase:
                spinner.start()
            try:
                event = next(stream)
            except StopIteration:
                break
            except MaxIterationsError:
                # The runaway backstop. Say so on both channels: the model's
                # answer is missing, and pretending otherwise would be a lie.
                spinner.stop()
                reasoning.close()
                yield TextDelta(STEP_LIMIT_NOTICE)
                return STEP_LIMIT_NOTICE, context_tokens
            spinner.stop()

            if not turn_open and isinstance(event, (ThinkingDelta, TextDelta, TurnEnd)):
                turn_open = True
                in_tool_phase = False
                spoke_this_turn = False
                step += 1
                # The [think] marker shows where each model roundtrip starts,
                # which makes batched-vs-chained tool calling visible.
                print(
                    style.paint(f"  [think] step {step}", style.DIM, sys.stderr),
                    file=sys.stderr,
                )

            if isinstance(event, ThinkingDelta):
                reasoning.write(event.text)
            elif isinstance(event, TextDelta):
                reasoning.close()
                if event.text:
                    spoke_this_turn = True
                    yield event
            elif isinstance(event, ToolCall):
                in_tool_phase = True
                if spoke_this_turn:
                    # The model spoke *and* called tools: close the spoken line
                    # so the tool trace doesn't glue onto it mid-line.
                    spoke_this_turn = False
                    yield TextDelta("\n")
                pending_args[event.id] = event.input
            elif isinstance(event, ToolResult):
                marker = style.paint(
                    f"  [tool] {event.name}",
                    style.RED if event.is_error else style.CYAN,
                    sys.stderr,
                )
                arguments = pending_args.pop(event.id, {})
                print(f"{marker}({arguments}) -> {_shorten(event.content)}", file=sys.stderr)
                # The next wait is the model's, so the spinner may resume.
                in_tool_phase = False
            elif isinstance(event, TurnEnd):
                turn_open = False
                reasoning.close()
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
                    print(
                        style.paint(
                            "  [note] the model ran out of tokens; this turn is cut off",
                            style.YELLOW,
                            sys.stderr,
                        ),
                        file=sys.stderr,
                    )
            elif isinstance(event, RunEnd):
                reply = event.result.text
                if reply and not spoke_this_turn:
                    # A provider that didn't stream its text still gets its
                    # answer displayed — see the display invariant above.
                    yield TextDelta(reply)
    finally:
        # Runs on normal end, on interrupt, and on generator close. Closing the
        # stream is what cancels in-flight tools and tears down the provider
        # connection when a caller walks away mid-turn.
        spinner.stop()
        reasoning.close()
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


class _ReasoningLine:
    """The live ``[reason]`` line on stderr — Punk Records murmuring.

    Opens on the first reasoning fragment and closes when the reply starts (or
    the stream ends), so the trace reads as one line per turn even though it's
    written as it's generated. Color opens once at the line start and resets
    once at the close, not per fragment: a Ctrl-C landing mid-reasoning — the
    likeliest interrupt point — would otherwise stain the whole terminal dim.
    """

    def __init__(self) -> None:
        self._open = False
        self._reset = ""

    def write(self, text: str) -> None:
        if not self._open:
            self._reset = style.RESET if style.enabled(sys.stderr) else ""
            open_code = style.DIM + style.MAGENTA if self._reset else ""
            print(f"{open_code}  [reason] ", end="", file=sys.stderr, flush=True)
            self._open = True
        print(text, end="", file=sys.stderr, flush=True)

    def close(self) -> None:
        """Close the line if it's open. Idempotent, so callers needn't check."""
        if self._open:
            self._open = False
            print(self._reset, file=sys.stderr)


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

    def start(self) -> None:
        if not sys.stderr.isatty() or self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._spin, daemon=True)
        self._thread.start()

    def _spin(self) -> None:
        for frame in itertools.cycle(self._FRAMES):
            # Draw before waiting, so even an instant stop() has one frame to
            # erase — which also makes the behavior deterministic to test.
            print(f"\r  {frame} thinking…", end="", file=sys.stderr, flush=True)
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


def _shorten(result: str, limit: int = 200) -> str:
    """Trim a tool result for the trace — display only; the model always gets
    the full result (capped separately by config.output_char_cap).

    200 chars keeps a whole-file read from flooding the watch channel while
    still showing enough to recognize what came back; hardcoded until someone
    actually needs to tune it.
    """
    extra = len(result) - limit
    if extra <= 0:
        return result
    return f"{result[:limit]}… (+{extra:,} more char{'s' if extra != 1 else ''})"

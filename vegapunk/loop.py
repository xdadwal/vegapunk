"""The agent loop — Vegapunk's think -> act -> observe cycle.

This is the heart of the agent and is deliberately hand-written: ask the brain
what to do, run any tool it requests, feed the result back, and repeat until it
returns a final answer (or we hit the safety limit that stops runaway loops).

The loop consumes the brain's event stream as it arrives: reasoning deltas are
traced live to stderr (the loop's watch-channel), while reply-text deltas are
re-yielded upward so the interface on top (the CLI) can render them token by
token. ``drive_turns`` is therefore a generator; its final reply travels back
as the generator's *return value* (``StopIteration.value``).
"""

from __future__ import annotations

import itertools
import sys
import threading
from collections.abc import Generator, Iterator
from concurrent.futures import ThreadPoolExecutor

from . import style
from .approval import Approver, Decision
from .brain import Brain, BrainResponse, ReasoningDelta, TextDelta, ThinkEvent, ToolCall
from .config import config
from .tools import Tool

# Results fed back when a guarded tool is not allowed to run. Both are worded to
# steer a small model away from immediately re-requesting the same tool.
DENIED = "Denied by the user. Do not retry this tool; consider another approach or ask the user."
NO_GATE = (
    "Blocked: this tool needs approval, but no approval gate is available here. "
    "Do not retry it; tell the user it can't run in this context."
)


def run(
    brain: Brain,
    tools: list[Tool],
    user_input: str,
    max_steps: int = config.max_steps,
    approver: Approver | None = None,
) -> str:
    """One-shot: run a single request to completion and return the reply.

    Drains the turn stream internally (no live rendering), so script callers
    keep the simple call-and-get-a-string contract.
    """
    messages: list[dict] = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": user_input},
    ]
    schemas = [tool.to_schema() for tool in tools]
    by_name = {tool.name: tool for tool in tools}
    turns = drive_turns(brain, by_name, schemas, messages, max_steps, approver)
    while True:
        try:
            next(turns)
        except StopIteration as stop:
            return stop.value


def drive_turns(
    brain: Brain,
    by_name: dict[str, Tool],
    schemas: list[dict],
    messages: list[dict],
    max_steps: int,
    approver: Approver | None = None,
) -> Generator[TextDelta, None, str]:
    """Run the think -> act -> observe loop over an existing messages list.

    A generator: yields ``TextDelta`` fragments of the assistant's speech as
    the model produces them, and *returns* the final text answer (or a notice
    if the step limit is hit) via ``StopIteration.value``. Mutates ``messages``
    in place (appending assistant and tool turns). Shared by the one-shot
    ``run()`` and the multi-turn ``Session`` so the loop logic lives in
    exactly one place.

    Display invariant: any reply text is always yielded as deltas *before*
    being returned — a non-streaming Brain's answer and the step-limit notice
    are synthesized into one delta each — so a renderer can print exactly what
    it receives and never needs to decide whether the return value still needs
    printing. Reasoning deltas are not re-yielded; they are traced live to
    stderr here, beside [think]/[tool], and never reach stdout or history.

    Tool calls the model batches into one turn are independent by definition,
    so they execute concurrently — tools must therefore be thread-safe. Guarded
    tools are approved first, in a sequential pre-pass, so an interactive
    approver never faces concurrent prompts (see ``_run_tool_batch``).
    """
    for step in range(max_steps):
        # Trace to stderr so you can *watch* the loop work (stdout stays clean);
        # the [think] marker shows where each model roundtrip starts, making
        # batched-vs-chained tool calling visible.
        print(style.paint(f"  [think] step {step + 1}", style.DIM, sys.stderr), file=sys.stderr)
        response, streamed_text = yield from _relay_think(brain.think(messages, tools=schemas))
        if response.truncated:
            # Out of tokens mid-answer: say so on the watch channel rather
            # than passing a silently amputated reply off as the model's
            # chosen ending.
            print(
                style.paint(
                    "  [note] the model ran out of tokens; this turn is cut off",
                    style.YELLOW,
                    sys.stderr,
                ),
                file=sys.stderr,
            )
        messages.append(response.message)  # OBSERVE: record what the model said

        if not response.tool_calls:
            if not streamed_text and response.text:
                # A Brain that didn't stream its text (yielded only the final
                # response) still gets its answer displayed — see the
                # display invariant above.
                yield TextDelta(response.text)
            return response.text or ""  # THINK said "done" — final answer

        if streamed_text:
            # The model spoke *and* called tools: close the spoken line so the
            # tool trace that follows doesn't glue onto it mid-line.
            yield TextDelta("\n")

        # ACT: run the turn's tools, then feed each result back into history.
        for call, result in _run_tool_batch(by_name, response.tool_calls, approver):
            marker = style.paint(
                f"  [tool] {call.name}",
                style.RED if _looks_failed(result) else style.CYAN,
                sys.stderr,
            )
            print(f"{marker}({call.arguments}) -> {_shorten(result)}", file=sys.stderr)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    notice = "(Stopped after hitting the step limit without a final answer.)"
    yield TextDelta(notice)
    return notice


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
        if not sys.stderr.isatty():
            return
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


def _looks_failed(result: str) -> bool:
    """Display-only guess at whether a tool result reports a failed step, so it
    can pop red in the trace. Matches every failure string this module's own
    producers emit: ``_run_tool`` ("Error…"), ``DENIED``, ``NO_GATE``
    ("Blocked…"), ``_unknown_tool`` ("No tool named…"), and ``_missing_args``
    ("<name> is missing required argument(s)…"). A decline *with feedback*
    ("The user declined…") deliberately stays un-red — that's a steer, not a
    failure. A successful result that merely starts with "Error" (a tool
    echoing a log) false-positives red; accepted, since this only tints the
    trace and the result fed back to the model is untouched.
    """
    return result.startswith(("Error", "Denied", "Blocked", "No tool named")) or (
        "is missing required argument(s)" in result
    )


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


def _relay_think(
    events: Iterator[ThinkEvent],
) -> Generator[TextDelta, None, tuple[BrainResponse, bool]]:
    """Consume one ``think`` stream: trace reasoning deltas live to stderr,
    re-yield text deltas to the caller, and return the final response plus
    whether any text was re-yielded.

    The [reason] line opens on the first reasoning fragment and closes when
    the reply starts (or the stream ends), so the live trace reads exactly
    like the old one-line-per-turn version — just written as it's generated.

    Color is opened once at the line start and reset once at the line close
    (not per fragment): the close lives in a ``finally`` because a Ctrl-C
    landing mid-reasoning — the likeliest interrupt point, blocked in the
    model read — would otherwise leave the whole terminal stained dim.
    """
    response: BrainResponse | None = None
    streamed_text = False
    reasoning_open = False
    # What closes the [reason] line; captured at open time so color-disabled
    # output stays byte-identical to the plain prints it replaced.
    reset = ""
    # Spin while the model chews on the prompt — the wait before its first
    # event is the one stretch of true silence a step has.
    spinner = _Spinner()
    spinner.start()
    try:
        for event in events:
            spinner.stop()  # first event arrived; idempotent on later ones
            if isinstance(event, ReasoningDelta):
                if not reasoning_open:
                    # Punk Records murmuring: dim magenta opens here and stays
                    # open across the raw deltas until the line-closing RESET.
                    reset = style.RESET if style.enabled(sys.stderr) else ""
                    open_code = style.DIM + style.MAGENTA if reset else ""
                    print(f"{open_code}  [reason] ", end="", file=sys.stderr, flush=True)
                    reasoning_open = True
                print(event.text, end="", file=sys.stderr, flush=True)
            elif isinstance(event, TextDelta):
                if reasoning_open:
                    print(reset, file=sys.stderr)
                    reasoning_open = False
                if event.text:
                    streamed_text = True
                    yield event
            elif isinstance(event, BrainResponse):
                response = event
    finally:
        # Runs on normal stream end, on interrupt, and on generator close —
        # a GeneratorExit always lands at a yield, where the line is already
        # closed, so this never emits spurious output during a close().
        spinner.stop()  # covers dying before the first event ever arrived
        if reasoning_open:
            print(reset, file=sys.stderr)
    if response is None:
        # A Brain that ends without its final event is a broken contract, not
        # a condition to paper over — fail loudly.
        raise RuntimeError("Brain.think() stream ended without a final BrainResponse")
    return response, streamed_text


def _run_tool_batch(
    by_name: dict[str, Tool], calls: list[ToolCall], approver: Approver | None = None
) -> list[tuple[ToolCall, str]]:
    """Gate guarded calls, then run the approved ones — concurrently when there
    is more than one.

    Gating is a *sequential* pre-pass (in call order) so an interactive approver
    never faces concurrent stdin prompts; only the actual running is concurrent.
    Read-only tools always run; an unknown tool name short-circuits to a
    corrective message naming the real tools. A guarded tool runs only if an
    approver says yes; if the user declines it short-circuits to ``DENIED`` — or
    to their own steer when they decline *with feedback*, fed back as the result
    so a decline can redirect rather than dead-end. If no approver is wired at all
    it short-circuits to ``NO_GATE`` — fail-closed, so a guarded tool never runs
    silently. Results stay keyed to the original call order, so the tool messages
    always line up with the assistant's tool_calls.
    """
    # Pre-pass: decide each call up front, in order, splitting into what runs
    # and what's blocked (with the reason fed back to the model).
    results: dict[int, str] = {}
    runnable: list[tuple[int, ToolCall]] = []
    for i, call in enumerate(calls):
        tool = by_name.get(call.name)
        if tool is None:
            results[i] = _unknown_tool(call.name, by_name)  # name the real tools so it can recover
        elif not tool.guarded:
            runnable.append((i, call))  # read-only — runs freely
        elif approver is None:
            results[i] = NO_GATE  # guarded, but nothing here can approve it
        else:
            decision: Decision = approver.approve(call.name, call.arguments)
            if decision.allow:
                runnable.append((i, call))
            elif decision.feedback:
                results[i] = _denied_with_feedback(decision.feedback)  # decline + steer
            else:
                results[i] = DENIED

    if len(runnable) == 1:
        i, call = runnable[0]
        results[i] = _run_tool(by_name.get(call.name), call.name, call.arguments)
    elif len(runnable) > 1:
        # Not a `with` block: its exit joins the workers, which would stall a
        # Ctrl-C until every in-flight tool finished.
        pool = ThreadPoolExecutor(max_workers=min(len(runnable), 8))
        try:
            futures = {
                i: pool.submit(_run_tool, by_name.get(call.name), call.name, call.arguments)
                for i, call in runnable
            }
            # _run_tool turns normal exceptions into error strings, so .result()
            # only re-raises interrupts (KeyboardInterrupt/SystemExit).
            for i, future in futures.items():
                results[i] = future.result()
        except BaseException:
            # Interrupted mid-batch: drop queued tools and stop waiting for running
            # ones (they can't be force-killed; they finish ignored) so the
            # interrupt reaches the caller promptly.
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown()

    return [(call, results[i]) for i, call in enumerate(calls)]


def _denied_with_feedback(feedback: str) -> str:
    """Frame the user's steer as an imperative tool result — the channel this
    model acts on — so declining a call redirects it instead of dead-ending."""
    return (
        f"The user declined this tool and said: {feedback}\n"
        "Do that instead — don't retry the same call."
    )


def _unknown_tool(name: str, by_name: dict[str, Tool]) -> str:
    """Feedback for a tool name the model invented: name the real tools so it can
    recover, rather than the call silently doing nothing."""
    available = ", ".join(sorted(by_name)) or "(none registered)"
    return (
        f"No tool named {name!r}. Available tools: {available}. "
        "Call one of these, or answer the user directly without a tool."
    )


def _missing_args(name: str, tool: Tool, missing: list[str]) -> str:
    """Feedback for required arguments the model left out: name them (with types,
    from the derived schema) and ask for a corrected retry."""
    props = tool.parameters.get("properties", {})
    listed = ", ".join(f'"{p}" ({props.get(p, {}).get("type", "string")})' for p in missing)
    return (
        f"{name} is missing required argument(s): {listed}. "
        f"Call {name} again with every required argument."
    )


def _run_tool(tool: Tool | None, name: str, arguments: dict) -> str:
    """Run a tool, turning any failure into a message the model can react to.

    Tools are a boundary we don't fully control (bad args, bugs, missing
    hardware), so a failure must never crash the loop — we feed the error back
    as the tool's result and let the model recover. Before running, a missing
    required argument short-circuits to corrective guidance (extra keys are
    tolerated — the @tool wrapper drops them), so a slightly-off call from a
    small model becomes a retry signal instead of an opaque TypeError.
    """
    if tool is None:
        return f"Error: no tool named {name!r}."  # unknown names are caught earlier; defensive
    missing = [p for p in tool.parameters.get("required", []) if p not in arguments]
    if missing:
        return _missing_args(name, tool, missing)
    try:
        return tool.run(arguments)
    except Exception as exc:  # noqa: BLE001 — boundary: surface it, don't crash
        return f"Error running {name}: {exc}"

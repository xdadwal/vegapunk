"""The agent loop — Vegapunk's think -> act -> observe cycle.

This is the heart of the agent and is deliberately hand-written: ask the brain
what to do, run any tool it requests, feed the result back, and repeat until it
returns a final answer (or we hit the safety limit that stops runaway loops).
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor

from .approval import Approver
from .brain import Brain, ToolCall
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
    messages: list[dict] = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": user_input},
    ]
    schemas = [tool.to_schema() for tool in tools]
    by_name = {tool.name: tool for tool in tools}
    return drive_turns(brain, by_name, schemas, messages, max_steps, approver)


def drive_turns(
    brain: Brain,
    by_name: dict[str, Tool],
    schemas: list[dict],
    messages: list[dict],
    max_steps: int,
    approver: Approver | None = None,
) -> str:
    """Run the think -> act -> observe loop over an existing messages list.

    Mutates ``messages`` in place (appending assistant and tool turns) and
    returns the final text answer, or a notice if the step limit is hit. Shared
    by the one-shot ``run()`` and the multi-turn ``Session`` so the loop logic
    lives in exactly one place.

    Tool calls the model batches into one turn are independent by definition,
    so they execute concurrently — tools must therefore be thread-safe. Guarded
    tools are approved first, in a sequential pre-pass, so an interactive
    approver never faces concurrent prompts (see ``_run_tool_batch``).
    """
    for step in range(max_steps):
        # Trace to stderr so you can *watch* the loop work (stdout stays clean);
        # the [think] marker shows where each model roundtrip starts, making
        # batched-vs-chained tool calling visible.
        print(f"  [think] step {step + 1}", file=sys.stderr)
        response = brain.think(messages, tools=schemas)
        if response.reasoning:
            # Watch the model think on the same suppressible channel as the rest
            # of the trace; it never enters history or the clean stdout reply.
            print(f"  [reason] {response.reasoning}", file=sys.stderr)
        messages.append(response.message)  # OBSERVE: record what the model said

        if not response.tool_calls:
            return response.text or ""  # THINK said "done" — final answer

        # ACT: run the turn's tools, then feed each result back into history.
        for call, result in _run_tool_batch(by_name, response.tool_calls, approver):
            print(f"  [tool] {call.name}({call.arguments}) -> {result}", file=sys.stderr)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    return "(Stopped after hitting the step limit without a final answer.)"


def _run_tool_batch(
    by_name: dict[str, Tool], calls: list[ToolCall], approver: Approver | None = None
) -> list[tuple[ToolCall, str]]:
    """Gate guarded calls, then run the approved ones — concurrently when there
    is more than one.

    Gating is a *sequential* pre-pass (in call order) so an interactive approver
    never faces concurrent stdin prompts; only the actual running is concurrent.
    Read-only tools always run; an unknown tool name short-circuits to a
    corrective message naming the real tools. A guarded tool runs only if an
    approver says yes; if the user declines it short-circuits to ``DENIED``, and
    if no approver is wired at all it short-circuits to ``NO_GATE`` — fail-closed,
    so a guarded tool never runs silently. Results stay keyed to the original
    call order, so the tool messages always line up with the assistant's tool_calls.
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
        elif approver.approve(call.name, call.arguments):
            runnable.append((i, call))
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

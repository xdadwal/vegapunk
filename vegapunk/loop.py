"""The agent loop — Vegapunk's think -> act -> observe cycle.

This is the heart of the agent and is deliberately hand-written: ask the brain
what to do, run any tool it requests, feed the result back, and repeat until it
returns a final answer (or we hit the safety limit that stops runaway loops).
"""

from __future__ import annotations

import sys

from .brain import Brain
from .config import config
from .tools import Tool


def run(brain: Brain, tools: list[Tool], user_input: str, max_steps: int = 6) -> str:
    messages: list[dict] = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": user_input},
    ]
    schemas = [tool.to_schema() for tool in tools]
    by_name = {tool.name: tool for tool in tools}

    for _ in range(max_steps):
        response = brain.think(messages, tools=schemas)
        messages.append(response.message)  # OBSERVE: record what the model said

        if not response.tool_calls:
            return response.text or ""  # THINK said "done" — final answer

        # ACT: run each requested tool, then feed the result back into history.
        for call in response.tool_calls:
            result = _run_tool(by_name.get(call.name), call.name, call.arguments)
            # Trace to stderr so you can *watch* the loop act (stdout stays clean).
            print(f"  [tool] {call.name}({call.arguments}) -> {result}", file=sys.stderr)
            messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

    return "(Stopped after hitting the step limit without a final answer.)"


def _run_tool(tool: Tool | None, name: str, arguments: dict) -> str:
    """Run a tool, turning any failure into a message the model can react to.

    Tools are a boundary we don't fully control (bad args, bugs, missing
    hardware), so a failure must never crash the loop — we feed the error back
    as the tool's result and let the model recover.
    """
    if tool is None:
        return f"Error: no tool named {name!r}."
    try:
        return tool.run(arguments)
    except Exception as exc:  # noqa: BLE001 — boundary: surface it, don't crash
        return f"Error running {name}: {exc}"

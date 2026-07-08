"""Throwaway smoke test for the Claude brain: real subscription, real network.

Not part of the pytest suite — run it by hand from the repo root when you want
to check the end-to-end path (auth, streaming, and the vega_tool fence):

    .venv/bin/python try_claude_brain.py

Auth comes from Claude Code: `claude /login` once on this machine, or set
CLAUDE_CODE_OAUTH_TOKEN (create one with `claude setup-token`).
"""

from vegapunk.brain import BrainResponse, TextDelta, create_brain
from vegapunk.config import config


def _run_turn(brain, messages, tools=None) -> BrainResponse:
    """Stream a turn to stdout as it generates, then return the finished turn."""
    response = None
    for event in brain.think(messages, tools=tools):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, BrainResponse):
            response = event
    print()
    return response


def main() -> None:
    brain = create_brain("claude")
    print(f"— plain turn ({brain.model_label}) —")
    _run_turn(
        brain,
        [
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": "Introduce yourself in one short sentence."},
        ],
    )

    print("— tool turn (should print a parsed ToolCall, not raw JSON) —")
    clock_tool = {
        "type": "function",
        "function": {
            "name": "clock",
            "description": "Tell the current date and time.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    response = _run_turn(
        brain,
        [
            {"role": "system", "content": config.system_prompt},
            {"role": "user", "content": "What time is it right now? Use the clock tool."},
        ],
        tools=[clock_tool],
    )
    print(f"tool_calls: {response.tool_calls}")
    print(f"history message: {response.message}")
    print(f"context_tokens: {response.context_tokens}")


if __name__ == "__main__":
    main()

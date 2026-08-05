"""Run one question through the full agent loop with tools.

Run from the repo root:

    .venv/bin/python try_agent.py
    .venv/bin/python try_agent.py "What's my battery at?"
"""

import sys

from logpose import Agent

from vegapunk.approval import CLIApprover
from vegapunk.backend import create_backend
from vegapunk.config import config
from vegapunk.gate import make_gate
from vegapunk.loop import run
from vegapunk.tools import ALL_TOOLS


def main() -> None:
    question = " ".join(sys.argv[1:]) or "How are you feeling right now?"
    backend = create_backend(config.provider)
    # Wire the gate: in a terminal you'll be prompted before a guarded tool
    # (write_file / run_shell) runs; piped/non-interactive, it auto-denies.
    agent = Agent(
        backend.provider,
        system=config.system_prompt,
        tools=ALL_TOOLS,
        max_iterations=config.max_steps,
        extra=backend.extra,
        on_tool_call=make_gate(CLIApprover()),
    )
    print(run(agent, question))


if __name__ == "__main__":
    main()

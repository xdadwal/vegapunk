"""Step 3 demo: run a question through the full agent loop with tools.

Run from the repo root:

    .venv/bin/python try_agent.py
    .venv/bin/python try_agent.py "What's my battery at?"
"""

import sys

from vegapunk.brain import DMRBrain
from vegapunk.loop import run
from vegapunk.tools import ALL_TOOLS


def main() -> None:
    question = " ".join(sys.argv[1:]) or "How are you feeling right now?"
    print(run(DMRBrain(), ALL_TOOLS, question))


if __name__ == "__main__":
    main()

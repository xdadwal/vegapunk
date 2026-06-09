"""Throwaway smoke test for Step 2: drive the model from our own Python code.

Run from the repo root:

    .venv/bin/python try_brain.py
"""

from vegapunk.brain import DMRBrain
from vegapunk.config import config


def main() -> None:
    brain = DMRBrain()
    messages = [
        {"role": "system", "content": config.system_prompt},
        {"role": "user", "content": "Introduce yourself in one short sentence."},
    ]
    # think() now returns a BrainResponse; .text is the assistant's reply.
    print(brain.think(messages).text)


if __name__ == "__main__":
    main()

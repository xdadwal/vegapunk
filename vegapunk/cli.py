"""Interactive REPL for Vegapunk — run with ``python -m vegapunk``.

A simple read-eval-print loop over a Session so you can hold a real
conversation. Tool activity is traced to stderr by the loop; replies go to
stdout.
"""

from __future__ import annotations

from .brain import DMRBrain
from .session import Session
from .tools import ALL_TOOLS

_EXIT = {"exit", "quit"}
_RESET = {"reset", "clear"}


def main() -> None:
    session = Session(DMRBrain(), ALL_TOOLS)
    print("Vegapunk interactive session. Type 'exit' to quit, 'reset' to clear history.")

    while True:
        try:
            user_input = input("you> ").strip()
        except EOFError:  # Ctrl-D
            print("\nbye.")
            return
        except KeyboardInterrupt:  # Ctrl-C while waiting for input
            print("\n(interrupted — type 'exit' to quit)")
            continue

        if not user_input:
            continue

        command = user_input.lower()
        if command in _EXIT:
            print("bye.")
            return
        if command in _RESET:
            session.reset()
            print("(history cleared)")
            continue

        try:
            reply = session.send(user_input)
        except KeyboardInterrupt:  # Ctrl-C mid-generation — cancel just this turn
            print("\n(interrupted)")
            continue
        print(f"vega> {reply}")


if __name__ == "__main__":
    main()

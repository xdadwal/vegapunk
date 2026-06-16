"""Interactive REPL for Vegapunk — run with ``python -m vegapunk``.

A simple read-eval-print loop over a Session so you can hold a real
conversation. Tool activity is traced to stderr by the loop; replies go to
stdout.
"""

from __future__ import annotations

from .approval import CLIApprover
from .brain import DMRBrain
from .prompter import Prompter, PromptToolkitPrompter
from .session import Session
from .tools import ALL_TOOLS

_EXIT = {"exit", "quit"}
_RESET = {"reset", "clear"}


def main(prompter: Prompter | None = None, session: Session | None = None) -> None:
    # Defaults are built here (not as argument defaults) so tests can inject a
    # scripted prompter / fake-brain session and never touch the model or a TTY.
    if session is None:
        # One approver for the whole REPL, so "always allow" lasts the session.
        session = Session(DMRBrain(), ALL_TOOLS, approver=CLIApprover())
    if prompter is None:
        prompter = PromptToolkitPrompter()
    print("Vegapunk interactive session. Type 'exit' to quit, 'reset' to clear history.")

    while True:
        try:
            user_input = prompter.prompt().strip()
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

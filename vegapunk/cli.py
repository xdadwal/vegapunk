"""Interactive REPL for Vegapunk — run with ``python -m vegapunk``.

A read-eval-print loop over a Session. ``/``-prefixed input is handled locally by
the slash-command system (``commands.py``); everything else is sent to the model.
Each conversation auto-saves every turn under a name the model picks from the
first message (``session_store``). Tool activity is traced to stderr by the loop;
replies stream to stdout token by token as the model generates them.
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime

from . import memory, session_store, style
from .approval import CLIApprover
from .brain import DMRBrain, TextDelta
from .commands import CommandContext, dispatch
from .config import config
from .prompter import Prompter, PromptToolkitPrompter
from .session import Session
from .tools import ALL_TOOLS


def _vega_prefix() -> str:
    """The reply-line prefix — Punk Records speaking, bold magenta. The reset
    lands before the space so the reply text itself streams in default color."""
    return style.paint("vega>", style.BOLD + style.MAGENTA, sys.stdout) + " "


def _context_gauge(used: int | None) -> str:
    """The toolbar's right side: how full the model's context window is —
    absolute tokens, and a percentage when the window size is known
    (config.context_window; 0 means unknown). Empty before the first turn."""
    if used is None:
        return ""
    if config.context_window > 0:
        # Deliberately uncapped: >100% means the conversation has overflowed
        # the configured window — capping would hide exactly that signal.
        pct = round(100 * used / config.context_window)
        return f"{used:,}/{config.context_window:,} tok ({pct}%) "
    return f"{used:,} tok "


def _status_line(ctx: CommandContext) -> str:
    """The prompt's bottom-toolbar text: model and conversation name on the
    left, context-window fullness on the right. Re-evaluated every render, so
    /save, /new, and each finished turn show up on the next prompt."""
    left = f" {config.model} · {ctx.current_name or 'unsaved'}"
    right = _context_gauge(ctx.session.context_tokens)
    # Right-align by padding to the terminal's current width; clamp so the
    # two sides never fuse when the window is narrow. len() counts code
    # points, not display cells — good enough because model ids and session
    # slugs are ASCII here; double-width glyphs would push the gauge off-edge.
    pad = shutil.get_terminal_size().columns - len(left) - len(right)
    return left + " " * max(pad, 1) + right


def main(prompter: Prompter | None = None, session: Session | None = None) -> None:
    # Defaults are built here (not as argument defaults) so tests can inject a
    # scripted prompter / fake-brain session and never touch the model or a TTY.
    if session is None:
        # One approver for the whole REPL, so "always allow" lasts the session.
        # Fold remembered facts into the system prompt so the model starts the
        # session already knowing them (no recall step needed).
        session = Session(
            DMRBrain(),
            ALL_TOOLS,
            system_prompt=config.system_prompt + memory.as_system_block(),
            approver=CLIApprover(),
        )
    # ctx exists before the prompter so the toolbar callable can close over
    # it — /save and /new then show up on the very next prompt render.
    ctx = CommandContext(session=session)
    if prompter is None:
        prompter = PromptToolkitPrompter(status=lambda: _status_line(ctx))
    print("Vegapunk interactive session. Type /help for commands, /exit to quit.")
    print(
        style.paint(
            f"model {config.model} · workspace {config.workspace_root}", style.DIM, sys.stdout
        )
    )

    while True:
        try:
            user_input = prompter.prompt().strip()
        except EOFError:  # Ctrl-D
            print("\nbye.")
            return
        except KeyboardInterrupt:  # Ctrl-C while waiting for input
            print("\n" + style.paint("(interrupted — type /exit to quit)", style.YELLOW, sys.stdout))
            continue

        if not user_input:
            continue

        result = dispatch(user_input, ctx)
        if result is not None:  # it was a slash command
            if result.output:
                print(result.output)
            if result.exit:
                return
            continue

        events = None
        try:
            # send() is a generator — nothing runs until the first next().
            # The loop guarantees the whole reply arrives as TextDeltas, so
            # rendering is just: print what you're handed, as you're handed it.
            events = session.send(user_input)
            streamed = False
            line_open = False
            while True:
                try:
                    event = next(events)
                except StopIteration:  # .value carries the reply; already rendered
                    break
                if isinstance(event, TextDelta) and event.text:
                    if not streamed:
                        print(_vega_prefix(), end="", flush=True)
                        streamed = True
                    print(event.text, end="", flush=True)
                    line_open = not event.text.endswith("\n")
            if not streamed:
                print(_vega_prefix())  # an empty reply still gets its prompt line
            elif line_open:
                print()
        except KeyboardInterrupt:  # Ctrl-C mid-generation — cancel just this turn
            if events is not None:
                # Closing throws GeneratorExit into the paused send(), which
                # rolls the partial turn out of history deterministically
                # (rather than whenever the abandoned generator gets GC'd).
                events.close()
            print("\n" + style.paint("(interrupted)", style.YELLOW, sys.stdout))
            continue
        _autosave_turn(ctx)


def _autosave_turn(ctx: CommandContext) -> None:
    """Persist the conversation after a turn, naming a fresh one from its first
    message (model-chosen, with a text-slug then timestamp fallback).

    Best-effort: a disk error — or a Ctrl-C during the titling call — degrades to
    a stderr note rather than tearing down the live conversation. The name is
    committed only after a successful first save, so '(saved as ...)' never lies.
    """
    try:
        if ctx.current_name is None:
            first = next(
                (m["content"] for m in ctx.session.messages if m.get("role") == "user" and m.get("content")),
                "",
            )
            base = (
                session_store.slugify(ctx.session.suggest_name())
                or session_store.slugify(first)
                or f"session-{datetime.now():%Y%m%d-%H%M%S}"
            )
            name = session_store.unique_name(base)
            session_store.save_session(name, ctx.session.messages)
            ctx.current_name = name
            print(style.paint(f"(saved as '{name}')", style.DIM, sys.stdout))
        else:
            session_store.save_session(ctx.current_name, ctx.session.messages)
    except KeyboardInterrupt:
        print(
            style.paint("  [session] autosave skipped (interrupted).", style.YELLOW, sys.stderr),
            file=sys.stderr,
        )
    except OSError as exc:
        print(
            style.paint(f"  [session] could not save: {exc}", style.RED, sys.stderr),
            file=sys.stderr,
        )


if __name__ == "__main__":
    main()

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

from . import db, embedding, memory, migrate, session_store, skills, style
from .approval import CLIApprover
from .brain import TextDelta, create_brain
from .commands import CommandContext, dispatch
from .config import config
from .prompter import Prompter, PromptToolkitPrompter
from .session import Session
from .tools import ALL_TOOLS


def _vega_prefix() -> str:
    """The reply-line prefix — Punk Records speaking, bold magenta. The reset
    lands before the space so the reply text itself streams in default color."""
    return style.paint("vega>", style.BOLD + style.MAGENTA, sys.stdout) + " "


def _context_gauge(used: int | None, window: int) -> str:
    """The toolbar's right side: how full the model's context window is —
    absolute tokens, and a percentage when the window size is known
    (0 means unknown). Empty before the first turn."""
    if used is None:
        return ""
    if window > 0:
        # Deliberately uncapped: >100% means the conversation has overflowed
        # the configured window — capping would hide exactly that signal.
        pct = round(100 * used / window)
        return f"{used:,}/{window:,} tok ({pct}%) "
    return f"{used:,} tok "


def _status_line(ctx: CommandContext) -> str:
    """The prompt's bottom-toolbar text: model and conversation name on the
    left, context-window fullness on the right. Re-evaluated every render, so
    /save, /new, /model, and each finished turn show up on the next prompt.
    Identity comes from the live brain, not config — /model swaps it."""
    left = f" {ctx.session.brain.model_label} · {ctx.current_name or 'unsaved'}"
    right = _context_gauge(ctx.session.context_tokens, ctx.session.brain.context_window)
    # Right-align by padding to the terminal's current width; clamp so the
    # two sides never fuse when the window is narrow. len() counts code
    # points, not display cells — good enough because model ids and session
    # slugs are ASCII here; double-width glyphs would push the gauge off-edge.
    pad = shutil.get_terminal_size().columns - len(left) - len(right)
    return left + " " * max(pad, 1) + right


def main(prompter: Prompter | None = None, session: Session | None = None) -> None:
    # Persistence setup, before anything reads the database: take the
    # single-process lock, fold any legacy flat files into the db, reconcile
    # embeddings with the configured model, and snapshot if the last backup is
    # stale. All but the lock are best-effort and never block the REPL.
    db.acquire_process_lock()
    migrate.migrate_if_needed()
    embedding.sync_embeddings()
    db.backup_if_stale()

    # Defaults are built here (not as argument defaults) so tests can inject a
    # scripted prompter / fake-brain session and never touch the model or a TTY.
    if session is None:
        # One approver for the whole REPL, so "always allow" lasts the session.
        # Fold remembered facts and the skill ads into the system prompt so the
        # model starts the session knowing both. Assembled once — a skill added
        # mid-session is reachable via use_skill but not advertised until the
        # next launch (same staleness memory has).
        session = Session(
            create_brain(config.provider),  # a bad VEGAPUNK_PROVIDER fails loudly here
            ALL_TOOLS,
            system_prompt=config.system_prompt + memory.as_system_block() + skills.as_system_block(),
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
            f"model {session.brain.model_label} · workspace {config.workspace_root}",
            style.DIM,
            sys.stdout,
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

        if ctx.pending_skill is not None:
            # A /skill staging rides this message: body first, imperatively
            # framed (the channel this model follows), then the request. The
            # closing marker keeps a body that ends in examples or quotes from
            # bleeding into the request. The combined turn enters history and
            # autosave as-is — an honest record of what the model actually saw.
            name, body = ctx.pending_skill
            user_input = (
                f"[Skill '{name}' — follow these instructions for this request:]\n"
                f"{body}\n[End of skill instructions. The request:]\n{user_input}"
            )
            ctx.pending_skill = None

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
        except Exception as exc:  # noqa: BLE001 — a failed turn must not kill the REPL
            # The turn is already rolled out of history (send()'s rollback),
            # so show the error — Claude auth failures arrive here with their
            # "run `claude /login`" hint — and keep the session (approvals,
            # /model, staged skills) alive for the user to recover.
            print("\n" + style.paint(f"[error] {exc}", style.RED, sys.stdout))
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

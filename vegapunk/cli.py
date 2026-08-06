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
from collections.abc import Generator

from logpose import TextDelta

from . import db, embedding, memory, session_store, skills, style, transcript, worker
from .approval import CLIApprover
from .backend import create_backend
from .commands import CommandContext, dispatch
from .gate import ApprovalCancelled
from .config import config
from .prompter import Prompter, PromptToolkitPrompter
from .render import Renderer, RichRenderer
from .session import Session
from .tools import ALL_TOOLS


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


def _is_rich_session(session: Session) -> bool:
    """Keep lightweight session doubles usable in CLI wiring tests."""
    return isinstance(getattr(session, "renderer", None), RichRenderer)


def _status_line(ctx: CommandContext) -> str:
    """The prompt's bottom-toolbar text: model and conversation name on the
    left, context-window fullness on the right. Re-evaluated every render, so
    /save, /new, /model, and each finished turn show up on the next prompt.
    Identity comes from the live brain, not config — /model swaps it."""
    left = f" {ctx.session.model_label} · {ctx.current_name or 'unsaved'}"
    right = _context_gauge(ctx.session.context_tokens, ctx.session.context_window)
    # Right-align by padding to the terminal's current width; clamp so the
    # two sides never fuse when the window is narrow. len() counts code
    # points, not display cells — good enough because model ids and session
    # slugs are ASCII here; double-width glyphs would push the gauge off-edge.
    pad = shutil.get_terminal_size().columns - len(left) - len(right)
    return left + " " * max(pad, 1) + right


def _render_reply(events: Generator[TextDelta, None, str], renderer: Renderer) -> None:
    """Stream a turn's reply through ``renderer``.

    Kept out of ``main``'s loop, which has enough going on. The pull-by-``next``
    shape is deliberate (rather than a ``for``) so a Ctrl-C landing between
    pulls still surfaces from here for ``main`` to catch and roll back.
    """
    while True:
        try:
            event = next(events)
        except StopIteration:  # .value carries the reply; already rendered
            break
        if isinstance(event, TextDelta):
            renderer.reply_delta(event.text)
    renderer.reply_end()


def main(prompter: Prompter | None = None, session: Session | None = None) -> None:
    # Persistence setup, before anything reads the database: take the
    # single-process lock, reconcile embeddings with the configured model, and
    # snapshot if the last backup is stale. All but the lock are best-effort and
    # never block the REPL.
    db.acquire_process_lock()
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
            create_backend(config.provider),  # a bad VEGAPUNK_PROVIDER fails loudly here
            ALL_TOOLS,
            system_prompt=config.system_prompt + memory.as_system_block() + skills.as_system_block(),
            approver=CLIApprover(),
        )
    # ctx exists before the prompter so the toolbar callable can close over
    # it — /save and /new then show up on the very next prompt render.
    ctx = CommandContext(session=session)
    if prompter is None:
        prompter = PromptToolkitPrompter(status=lambda: _status_line(ctx))
    if _is_rich_session(session):
        print(style.paint("── Vegapunk · /help for commands · /exit to quit", style.DIM, sys.stdout))
        print(
            style.paint(
                f"   model {session.model_label} · workspace {config.workspace_root}",
                style.DIM,
                sys.stdout,
            )
        )
    else:
        print("Vegapunk interactive session. Type /help for commands, /exit to quit.")
        print(
            style.paint(
                f"model {session.model_label} · workspace {config.workspace_root}",
                style.DIM,
                sys.stdout,
            )
        )

    # Scheduled tasks run in their own process, not a thread here: nothing to
    # serialize against your turns, and its trace goes to its own log instead of
    # landing on top of your prompt. Coordination is entirely through the
    # database — /schedule writes rows, the worker polls them.
    scheduler, scheduler_log = worker.start()
    warned_worker_died = False
    try:
        while True:
            # A worker that died (lock contention, bad model config) would
            # otherwise mean scheduled tasks silently stop happening. Said once,
            # before the prompt, pointing at the log that explains why.
            if scheduler is not None and scheduler.poll() is not None and not warned_worker_died:
                print(
                    style.paint(
                        f"[scheduler] worker exited ({scheduler.returncode}) — scheduled tasks "
                        f"are not running; see {scheduler_log}",
                        style.YELLOW,
                        sys.stdout,
                    )
                )
                warned_worker_died = True

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
                _render_reply(events, session.renderer)
            except (KeyboardInterrupt, ApprovalCancelled):
                # Ctrl-C mid-generation — cancel just this turn. Ctrl-C at the
                # approval prompt arrives as ApprovalCancelled instead, because
                # that prompt runs on logpose's loop thread where a real
                # KeyboardInterrupt would tear the loop down noisily.
                if events is not None:
                    # Closing throws GeneratorExit into the paused send(), which
                    # rolls the partial turn out of history deterministically
                    # (rather than whenever the abandoned generator gets GC'd).
                    events.close()
                # The renderer's own reply_end() would print here (a pending
                # newline, or a bare prompt line) — silently, before the
                # "(interrupted)" message below. reply_abort() resets its state
                # without printing, so the next turn still gets its prefix.
                session.renderer.reply_abort()
                print("\n" + style.paint("(interrupted)", style.YELLOW, sys.stdout))
                continue
            except Exception as exc:  # noqa: BLE001 — a failed turn must not kill the REPL
                # The turn is already rolled out of history (send()'s rollback),
                # so show the error — Claude auth failures arrive here with their
                # "run `claude /login`" hint — and keep the session (approvals,
                # /model, staged skills) alive for the user to recover.
                session.renderer.reply_abort()  # see reply_abort's docstring
                if _is_rich_session(session):
                    print("\n" + style.paint(f"✕ error · {exc}", style.RED, sys.stdout))
                else:
                    print("\n" + style.paint(f"[error] {exc}", style.RED, sys.stdout))
                continue
            _autosave_turn(ctx)
            if _is_rich_session(session):
                print(style.paint("──", style.DIM, sys.stdout))
    finally:
        # Every exit path (/exit, Ctrl-D, or an error escaping the loop). The
        # worker also watches for us dying, but that is the kill -9 backstop —
        # stopping it here is what makes an ordinary quit leave nothing behind.
        # The session holds a private event-loop thread of its own; closing it
        # is what lets the interpreter exit promptly rather than at daemon-thread
        # teardown.
        worker.stop(scheduler)
        session.close()


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
                (
                    transcript.text_of(m)
                    for m in ctx.session.messages
                    if transcript.is_user_turn(m)
                ),
                "",
            )
            name = session_store.choose_name(ctx.session.suggest_name(), first)
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

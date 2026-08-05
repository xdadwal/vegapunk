"""The scheduler worker — the process that actually runs due tasks.

Run as ``python -m vegapunk.scheduler_worker``, though normally you don't: the
REPL spawns one at startup and stops it on exit (see ``vegapunk/worker.py``),
pointing it at the same database through ``VEGAPUNK_DB_FILE``.

Why a separate process rather than the thread this used to be. A scheduled run is
a full agent turn, and in the REPL's process it competed with you for the one
model and the one connection — so the two had to take turns behind a lock, and a
task already in flight made your next turn wait. Worse, its ``[think]``/``[tool]``
trace went to the same stderr your prompt lives on. Neither problem is solvable
in-process: a model call can't be preempted, and ``sys.stderr`` is global, so
redirecting it in a background thread would swallow the foreground's trace too.
Out here both dissolve — no lock, and the trace goes to this process's own log.

Two guards keep exactly one worker alive and no more. ``acquire_scheduler_lock``
turns away a second worker (its own lock file, so it can coexist with the REPL's).
And a watchdog notices if the REPL that spawned us dies without stopping us — a
``kill -9`` that ``cli``'s shutdown path can't cover — since being reparented is
visible as ``getppid()`` changing.
"""

from __future__ import annotations

import os
import signal
import sys
import threading
from dataclasses import replace

from logpose import Agent

from . import db
from .backend import Backend, create_backend, with_effort
from .config import config
from .gate import make_gate
from .scheduler import Scheduler
from .tools import ALL_TOOLS

# How often the watchdog checks whether the REPL that spawned us is still there.
# Independent of (and much shorter than) the task poll: a worker whose parent is
# gone should exit promptly rather than linger until the next tick.
_PARENT_CHECK_SECONDS = 5.0


def parse_model_spec(spec: str) -> tuple[str, str]:
    """Split a "provider[:model]" spec into its parts.

    One spelling for both the ``VEGAPUNK_SCHEDULER_MODEL`` env var and the
    ``/schedule --model`` flag, so "claude:opus" means the same thing in either.
    """
    provider, _, model = spec.partition(":")
    provider = provider.strip().lower()
    if provider not in ("local", "claude"):
        raise ValueError(f"Unknown provider {provider!r} — expected 'local' or 'claude'.")
    return provider, model.strip()


def build_backend() -> Backend:
    """The backend every scheduled run uses, from config.

    ``VEGAPUNK_SCHEDULER_MODEL`` (``provider[:model]``) wins; unset, the worker
    inherits the provider/model the REPL was launched with, since it inherits the
    environment. Effort likewise falls back to ``VEGAPUNK_CLAUDE_EFFORT``.

    Inheriting is the right default rather than pinning to ``local``: someone
    running ``VEGAPUNK_PROVIDER=claude`` usually does so *because* no local model
    server is up, and a hardcoded local default would fail every task for them.
    Pinning unattended runs to the free model is then one variable away.

    Raises ``ValueError`` for a malformed spec — ``main`` turns that into a clean
    exit rather than a traceback, so the REPL's "worker exited" note is the story.
    """
    if config.scheduler_model:
        provider, model = parse_model_spec(config.scheduler_model)
    else:
        provider, model = config.provider, config.claude_model
    backend = create_backend(provider, replace(config, claude_model=model) if model else config)
    effort = config.scheduler_effort or config.claude_effort
    if effort:
        # Asking for an effort level on a backend that has none is a config
        # mismatch worth saying out loud rather than dropping silently.
        if backend.supports_effort:
            backend = with_effort(backend, effort)
        else:
            print(
                f"  [scheduler] ignoring effort {effort!r} — {provider} has no effort setting",
                file=sys.stderr,
            )
    return backend


def build_agent(backend: Backend) -> Agent:
    """The agent scheduled runs go through: every tool, and no approver.

    ``make_gate(None)`` is the fail-closed half of that — a guarded tool is
    blocked rather than run with nobody watching.
    """
    return Agent(
        backend.provider,
        system=config.system_prompt,
        tools=ALL_TOOLS,
        max_iterations=config.max_steps,
        extra=backend.extra,
        on_tool_call=make_gate(None),
    )


def watch_parent(stop: threading.Event, parent_pid: int, interval: float = _PARENT_CHECK_SECONDS) -> None:
    """Set ``stop`` once the process that spawned us is gone.

    The backstop for the exit path ``cli`` can't cover: it terminates the worker
    on ``/exit``, Ctrl-D, and errors, but a ``kill -9``'d REPL would otherwise
    leave this polling forever. ``parent_pid`` is captured before the wait so a
    parent that dies during startup is still noticed.
    """
    while not stop.wait(interval):
        if os.getppid() != parent_pid:
            print("  [scheduler] the REPL that started this worker is gone", file=sys.stderr)
            stop.set()
            return


def main() -> None:
    """Take the worker lock, build the agent, and poll until told to stop."""
    db.acquire_scheduler_lock()  # exits(1) if another worker already holds it
    try:
        backend = build_backend()
        agent = build_agent(backend)
    except ValueError as exc:
        print(f"  [scheduler] bad model configuration: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    stop = threading.Event()
    # SIGTERM is how cli stops us; SIGINT covers a hand-run worker's Ctrl-C. Both
    # ask the ticker to finish its current task rather than dropping it mid-turn.
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    threading.Thread(
        target=watch_parent, args=(stop, os.getppid()), name="parent-watchdog", daemon=True
    ).start()

    # Logged, not silent: these are the two facts you need when a scheduled run
    # used a model you didn't expect, and the log is the only place to see them.
    print(
        f"  [scheduler] worker {os.getpid()} up — model {backend.model_label}, db {db.db_path()}",
        file=sys.stderr,
    )
    Scheduler(lambda: agent, stop=stop).serve()
    print(f"  [scheduler] worker {os.getpid()} stopped", file=sys.stderr)


if __name__ == "__main__":
    main()

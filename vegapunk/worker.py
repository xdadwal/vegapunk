"""The parent side of the scheduler worker process.

``scheduler_worker`` is what runs *inside* that process; this is how the REPL
spawns one at startup and stops it on exit. The two halves live apart on purpose:
the REPL only ever needs to start and stop a subprocess, and importing the
worker's own module to do that would drag the scheduler, the backend, and every
tool into a session that may never schedule anything.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Imported by name, not via the module, so a test can swap the spawn without
# reaching into the stdlib module every other subprocess user shares.
from subprocess import DEVNULL, Popen, TimeoutExpired

from . import db, style


def start() -> tuple[Popen | None, Path]:
    """Spawn the scheduler worker, returning it and the log it writes to.

    Its output goes to ``scheduler.log`` beside the database rather than to this
    terminal — that separation is the entire reason the worker is a process, so
    letting it inherit our stderr would give it all back. The database path is
    passed explicitly rather than relying on a shared cwd, so parent and child can
    never disagree about which file they mean.

    Returns ``None`` for the process if it couldn't be spawned: a session without
    scheduled tasks is degraded, not broken, so the REPL reports it and carries on
    instead of refusing to start.
    """
    log_path = db.db_path().parent / "scheduler.log"
    log = None
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "a", buffering=1)  # line-buffered: tail -f shows ticks live
        proc = Popen(
            [sys.executable, "-m", "vegapunk.scheduler_worker"],
            stdin=DEVNULL,
            stdout=log,
            stderr=log,
            env={**os.environ, "VEGAPUNK_DB_FILE": str(db.db_path())},
        )
    except OSError as exc:
        print(
            style.paint(
                f"  [scheduler] could not start the worker ({exc}) — scheduled tasks are off",
                style.YELLOW,
                sys.stderr,
            ),
            file=sys.stderr,
        )
        return None, log_path
    finally:
        # The child holds its own duplicate of this handle, so the parent's copy
        # is dead weight for the life of the session once the spawn is done.
        if log is not None:
            log.close()
    return proc, log_path


def stop(worker: Popen | None, timeout: float = 5.0) -> None:
    """Stop the scheduler worker, escalating to a kill if it doesn't go.

    SIGTERM first: the ticker checks for a stop between tasks, so a worker that is
    merely idling exits at once. A worker *inside* a task can't honor it — the stop
    flag isn't read again until the model call returns, and a turn routinely
    outlasts ``timeout`` — so that one gets killed, and the run is lost.

    Losing it is the cheap outcome, deliberately chosen over the alternatives:
    ``record_run`` never ran, so the task keeps its old ``next_run_at`` and simply
    runs again later. Waiting out a full turn would hang ``/exit`` for as long as
    the model felt like taking, and leaving the worker to finish would block the
    *next* session's worker on the scheduler lock.
    """
    if worker is None or worker.poll() is not None:
        return
    worker.terminate()
    try:
        worker.wait(timeout)
    except TimeoutExpired:
        worker.kill()
        print(
            style.paint(
                "  [scheduler] a task was still running — it was stopped and stays due",
                style.DIM,
                sys.stderr,
            ),
            file=sys.stderr,
        )

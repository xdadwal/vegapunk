"""Thread-safe delegate progress, with a terminal-only live activity panel."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from threading import Event, Lock, Thread

from logpose import TurnEnd
from rich.console import Console
from rich.live import Live
from rich.text import Text

from . import terminal


@dataclass(frozen=True)
class DelegateRun:
    id: str
    role: str
    model: str
    started: float
    status: str = "running"
    ended: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    usage_reported: bool = False


class DelegateActivity:
    """Keep distinct invocations, including concurrent calls to the same role."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = Lock()
        self._runs: dict[str, DelegateRun] = {}
        self._next = 0

    def start(self, role: str, model: str) -> str:
        with self._lock:
            self._next += 1
            key = str(self._next)
            # Bound retained history without dropping any still-running call.
            done = [key for key, run in self._runs.items() if run.ended is not None]
            for old in done[:-19]:
                del self._runs[old]
            self._runs[key] = DelegateRun(key, role, model, self._clock())
            return key

    def status(self, key: str, status: str) -> None:
        with self._lock:
            if self._runs[key].ended is None:
                self._runs[key] = replace(self._runs[key], status=status)

    def model(self, key: str, model: str) -> None:
        with self._lock:
            self._runs[key] = replace(self._runs[key], model=model)

    def usage(self, key: str, event: TurnEnd) -> None:
        usage = event.usage
        with self._lock:
            run = self._runs[key]
            self._runs[key] = replace(
                run, input_tokens=run.input_tokens + usage.input_tokens,
                output_tokens=run.output_tokens + usage.output_tokens,
                cache_read=run.cache_read + usage.cache_read_input_tokens,
                cache_write=run.cache_write + usage.cache_creation_input_tokens,
                usage_reported=run.usage_reported or any((usage.input_tokens, usage.output_tokens,
                                                       usage.cache_read_input_tokens,
                                                       usage.cache_creation_input_tokens)),
            )

    def finish(self, key: str, status: str) -> None:
        with self._lock:
            self._runs[key] = replace(self._runs[key], status=status, ended=self._clock())

    def snapshot(self) -> tuple[DelegateRun, ...]:
        with self._lock:
            return tuple(self._runs.values())

    def lines(self, runs: tuple[DelegateRun, ...] | None = None) -> list[str]:
        now = self._clock()
        rows = []
        for run in self.snapshot() if runs is None else runs:
            seconds = max(0, (run.ended if run.ended is not None else now) - run.started)
            usage = (f"{run.input_tokens:,} in / {run.output_tokens:,} out"
                     if run.usage_reported else "usage not reported")
            if run.cache_read or run.cache_write:
                usage += f" · cache {run.cache_read:,} read / {run.cache_write:,} write"
            rows.append(f"{run.role} #{run.id} · {run.status} · {seconds:.1f}s · {usage} · {run.model}")
        return rows

    def compact(self) -> str:
        """One activity line, suitable for the composer's small status area."""
        runs = tuple(run for run in self.snapshot() if run.ended is None)
        if not runs:
            return ""
        now = self._clock()
        names = ", ".join(f"{run.role} {max(0, now - run.started):.0f}s" for run in runs[:2])
        if len(runs) > 2:
            names += f", +{len(runs) - 2}"
        reported = any(run.usage_reported for run in runs)
        count = sum(run.input_tokens + run.output_tokens + run.cache_read + run.cache_write
                    for run in runs)
        tokens = f"{count:,} reported tok" if reported else "tokens n/a"
        missing = sum(not run.usage_reported for run in runs)
        if reported and missing:
            tokens += f" · {missing} n/a"
        waiting = sum(run.status.startswith("approval:") for run in runs)
        suffix = f" · {waiting} awaiting approval" if waiting else ""
        label = "agent" if len(runs) == 1 else "agents"
        stopping = sum(run.status == "stopping" for run in runs)
        if stopping:
            suffix += f" · stopping {stopping}"
        return f"{len(runs)} {label} · {names} · {tokens}{suffix}"


class DelegateDisplay:
    """Refresh elapsed time while yielding the terminal to approval pickers."""

    def __init__(self, activity: DelegateActivity, console: Console | None = None,
                 *, session_status: Callable[[], str] | None = None) -> None:
        self.activity = activity
        self.console = console or Console(stderr=True)
        self._live: Live | None = None
        self._stop = Event()
        self._thread: Thread | None = None
        self._shown: set[str] = set()
        self._session_status = session_status

    def start(self) -> None:
        terminal.set_pause_handler(self.pause)
        self._thread = Thread(target=self._refresh, name="delegate-display", daemon=True)
        self._thread.start()

    def pause(self) -> None:
        """Caller holds terminal.LOCK while another UI owns the cursor."""
        self._stop_live()
        # Flush before a spinner or input widget takes over, never later from
        # the refresh thread while the main prompt owns the terminal.
        runs = self.activity.snapshot()
        for run, line in zip(runs, self.activity.lines(runs)):
            if run.ended is not None and run.id not in self._shown:
                # Keep the durable trace compact too; detailed fields remain
                # available on the activity snapshot and runtime logs.
                brief = line.rsplit(" · ", 1)[0]
                self.console.print(Text(f"  [agent] {brief}"))
                self._shown.add(run.id)

    def _stop_live(self) -> None:
        if self._live is not None:
            self._live.stop()
            self._live = None

    def _draw(self) -> None:
        runs = self.activity.snapshot()
        if not runs:
            return
        if self.console.is_terminal and any(run.ended is None for run in runs):
            if self._live is None:
                self._live = Live(console=self.console, auto_refresh=False, transient=True,
                                  redirect_stdout=False, redirect_stderr=False)
                self._live.start()
            rows = Text()
            if self._session_status is not None:
                # The normal prompt gauge contains alignment padding; keep the
                # live session row compact and separate from delegate metrics.
                stats = " ".join(self._session_status().split())
                rows.append("  Session: " + stats + "\n", style="dim")
            rows.append("  Sub-agents: " + self.activity.compact() + " · Ctrl-C stop all")
            self._live.update(rows, refresh=True)
        else:
            self._stop_live()

    def _refresh(self) -> None:
        while not self._stop.wait(0.2):
            if terminal.LOCK.acquire(timeout=0.05):
                try:
                    self._draw()
                finally:
                    terminal.LOCK.release()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        with terminal.LOCK:
            self.pause()
            self._draw()
            self.pause()
            terminal.set_pause_handler(None)

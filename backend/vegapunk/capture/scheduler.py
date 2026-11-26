"""Runs the collectors on a timer inside the server and records how each one is doing."""

import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from vegapunk.capture.collectors import Collector
from vegapunk.capture.ingest import ingest
from vegapunk.capture.rollup import prune, rollup
from vegapunk.db import connect, transaction

log = logging.getLogger(__name__)

THREAD_NAME = "vegapunk-capture"
PRUNE_EVERY = timedelta(hours=1)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Scheduler:
    def __init__(
        self,
        db_path: Path,
        collectors: Sequence[Collector],
        interval: int,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._db_path = db_path
        self._collectors = tuple(collectors)
        self._interval = interval
        self._clock = clock
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_prune: datetime | None = None

    def run_once(self) -> None:
        """One cycle. A source that fails never stops the others, and nothing escapes."""
        now = self._clock()
        try:
            conn = connect(self._db_path)
        except Exception:
            log.exception("Capture could not open the database")
            return
        try:
            stored = False
            for collector in self._collectors:
                try:
                    stored |= self._run_collector(conn, collector, now)
                except Exception:
                    log.exception("Capture failed for %s", collector.name)
            self._maintain(conn, now, stored)
        finally:
            conn.close()

    def _run_collector(self, conn: sqlite3.Connection, collector: Collector, now: datetime) -> bool:
        """Returns True if the collector's records were stored."""
        try:
            records = collector.collect(now)
        except Exception as exc:
            log.warning("Capture from %s failed: %s", collector.name, exc)
            self._failed(conn, collector.name, now, str(exc) or type(exc).__name__)
            return False
        try:
            ingest(conn, now, records)
        except Exception as exc:
            log.exception("Capture from %s could not be stored", collector.name)
            self._failed(conn, collector.name, now, f"Couldn't store data: {exc}")
            return False
        with transaction(conn):
            conn.execute(
                "INSERT INTO capture_status (source, last_attempt_at, last_success_at, last_error) "
                "VALUES (?, ?, ?, NULL) ON CONFLICT (source) DO UPDATE SET "
                "last_attempt_at = excluded.last_attempt_at, "
                "last_success_at = excluded.last_success_at, last_error = NULL",
                (collector.name, now.isoformat(), now.isoformat()),
            )
        return True

    def _maintain(self, conn: sqlite3.Connection, now: datetime, stored: bool) -> None:
        try:
            if stored:
                rollup(conn, now)
            if self._last_prune is None or now - self._last_prune >= PRUNE_EVERY:
                prune(conn, now)
                self._last_prune = now
        except Exception:
            log.exception("Capture maintenance failed")

    @staticmethod
    def _failed(conn: sqlite3.Connection, source: str, now: datetime, error: str) -> None:
        with transaction(conn):
            conn.execute(
                "INSERT INTO capture_status (source, last_attempt_at, last_success_at, last_error) "
                "VALUES (?, ?, NULL, ?) ON CONFLICT (source) DO UPDATE SET "
                "last_attempt_at = excluded.last_attempt_at, last_error = excluded.last_error",
                (source, now.isoformat(), error),
            )

    def _loop(self) -> None:
        while not self._stop.is_set():
            started = time.monotonic()
            self.run_once()
            # Measured from the start of the cycle: a slow one shortens the wait and the
            # next never overlaps it.
            if self._stop.wait(max(0.0, self._interval - (time.monotonic() - started))):
                return

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name=THREAD_NAME, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

"""The embedded SQLite database — one file for sessions, memory, and input history.

Every other module talks to the database *only* through this module's driver-free
surface (``query``/``execute``/``transaction`` plus the small helpers), so the
``import sqlite3`` line and the driver's exception type live in exactly one place.
That kept the driver swappable, which is how this module moved off the beta
``pyturso`` driver without a line changing elsewhere, and it lets callers handle
failures through a single ``StoreError`` (which subclasses ``OSError`` so existing
best-effort handlers keep working).

One connection, serialized by ``_conn_lock``. ``sqlite3.threadsafety`` is 1 on a
stock CPython build — SQLite compiled multi-thread, not serialized — so a single
connection must never be used by two threads at once, and two threads genuinely do
reach it: the scheduler's ticker runs a task's turn while the main thread sits at
the prompt, where ``db_history`` reads and writes ``input_history`` *outside* the
lock the CLI wraps its turns in. So the serialization lives here, in the module
that owns the connection, rather than being spread across callers who would each
have to know to bring their own. ``check_same_thread=False`` is paired with that
lock, not a substitute for it.

Single process at a time: ``acquire_process_lock`` takes an advisory lock at
startup. That is now *policy*, not a driver limit — the file is standard SQLite in
WAL mode, which handles concurrent processes correctly — and it is the guard the
scheduler-worker split will lift. WAL also means any ``sqlite3`` client can read
the file (or a ``/backup`` snapshot) while Vegapunk is running, which is the
recovery path.
"""

from __future__ import annotations

import math
import os
import sqlite3
import struct
import sys
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # non-Unix; the single-process guard becomes a no-op with a note
    fcntl = None  # type: ignore[assignment]

from .config import config

SCHEMA_VERSION = 1

# Sessions store the message list as a JSON blob; memory rows carry an open
# ``kind`` and an optional embedding for semantic recall. Kept free of SQL
# comments so ``executescript`` stays maximally portable across the driver.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    slug TEXT PRIMARY KEY,
    messages TEXT NOT NULL,
    turns INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memory (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL DEFAULT 'fact',
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata TEXT,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_memory_kind ON memory(kind, created_at);
CREATE TABLE IF NOT EXISTS input_history (
    id INTEGER PRIMARY KEY,
    entry TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    next_run_at TEXT NOT NULL,
    last_run_at TEXT,
    last_status TEXT,
    last_result TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scheduled_due ON scheduled_tasks(enabled, next_run_at);
"""


class StoreError(OSError):
    """The database is unavailable or a statement failed.

    Subclasses ``OSError`` deliberately: ``cli._autosave_turn``'s existing
    ``except OSError`` then handles save failures unchanged, and the REPL keeps
    running with persistence degraded rather than crashing.
    """


def db_path() -> Path:
    """The database file's path — a function (not a constant) so tests can
    monkeypatch it, mirroring the old ``sessions_dir()``/``memory_path()`` seams."""
    return config.db_file


# The one place the timestamp format lives. utcnow() and utcnow_plus() share it
# so a future format change can't desync "now" stamps from "due" stamps.
_STAMP_FORMAT = "%Y-%m-%dT%H:%M:%S.%fZ"


def utcnow() -> str:
    """ISO-8601 UTC with microseconds, e.g. ``2026-07-09T12:34:56.123456Z``.

    Fixed width and lexicographically sortable; the microseconds keep same-second
    rows ordered without a separate sequence column.
    """
    return datetime.now(timezone.utc).strftime(_STAMP_FORMAT)


def utcnow_plus(seconds: float) -> str:
    """The ``utcnow()`` stamp for ``seconds`` in the future — same format and
    width, so it compares lexicographically against ``utcnow()`` to answer "is
    this scheduled task due yet?" without a datetime round-trip at the comparison.
    """
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(_STAMP_FORMAT)


def stamp_plus(stamp: str, seconds: float) -> str:
    """The stamp ``seconds`` after a given ``utcnow()``-format ``stamp`` — same
    format and width, so the result stays lexicographically comparable.

    Lets a caller advance a schedule from a *recorded* instant rather than only
    from wall-clock now, so the "ran at" and "next due" stamps derive from one
    clock read instead of two (see ``scheduler.record_run``). Parses via the
    shared ``_STAMP_FORMAT`` so parse and format can't drift apart.
    """
    parsed = datetime.strptime(stamp, _STAMP_FORMAT).replace(tzinfo=timezone.utc)
    return (parsed + timedelta(seconds=seconds)).strftime(_STAMP_FORMAT)


def new_id() -> str:
    """A fresh opaque identifier (uuid4 hex) — no rowid dependence, so rows stay
    stable under a future replication/sync scheme."""
    return uuid.uuid4().hex


_conn: sqlite3.Connection | None = None
_conn_path: Path | None = None

# Guards the one connection against concurrent use from two threads — see the
# module docstring on why that can't be left to callers. Re-entrant because
# ``transaction()`` holds it across a caller's whole block, and code inside that
# block may reach back into this module's own helpers on the same thread.
_conn_lock = threading.RLock()

# How long a statement waits for another writer's lock before giving up with
# "database is locked". Matters across *processes* (WAL admits one writer at a
# time), so it is what keeps a scheduler worker and the REPL from failing writes
# on contention rather than briefly queueing.
_BUSY_TIMEOUT_MS = 5000


# Returned for any pair of vectors that can't be compared. Real cosine distance
# tops out at 2.0 (exactly opposed vectors), so this sits strictly past every real
# answer and sorts such a row last under ``ORDER BY distance`` — where SQL NULL
# would sort it *first*.
_UNRANKABLE_DISTANCE = 3.0


def _vector_distance_cos(a: bytes | None, b: bytes | None) -> float:
    """Cosine distance (1 - cosine similarity) between two float32 blobs.

    Turso shipped this as a built-in SQL function and stdlib SQLite has no vector
    support at all, so ``memory``'s semantic-recall ``ORDER BY`` gets it from here
    — this module being the one place that knows what the driver does and doesn't
    provide. Vectors are the little-endian float32 blobs ``embedding.pack`` writes.

    Unrankable input yields ``_UNRANKABLE_DISTANCE`` rather than an error or NULL:
    a missing blob, a length mismatch (an embed-model change caught mid-flight), or
    a zero vector, whose cosine is undefined. One odd row must neither poison the
    whole query — the caller would fall back to plain text matching — nor float to
    the top of the results.
    """
    if not a or not b or len(a) != len(b) or len(a) % 4:
        return _UNRANKABLE_DISTANCE
    count = len(a) // 4  # equal lengths, whole float32s: unpack can't fail past here
    va = struct.unpack(f"<{count}f", a)
    vb = struct.unpack(f"<{count}f", b)
    norms = math.sqrt(sum(x * x for x in va)) * math.sqrt(sum(y * y for y in vb))
    if norms == 0.0:
        return _UNRANKABLE_DISTANCE
    return 1.0 - sum(x * y for x, y in zip(va, vb)) / norms


def get_connection() -> sqlite3.Connection:
    """Return the process-wide connection, opening + bootstrapping it on first use.

    Keyed on ``db_path()``: when the seam changes (how tests get isolation), the
    old connection is closed and a new one opened. Raises ``StoreError`` if the
    file can't be opened or the on-disk schema is newer than this code.
    """
    global _conn, _conn_path
    with _conn_lock:
        path = db_path()
        if _conn is not None and _conn_path == path:
            return _conn
        close_connection()
        conn: sqlite3.Connection | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            # check_same_thread=False because several threads legitimately share
            # this connection; _conn_lock — not the driver's own check — is what
            # keeps them off it at the same time.
            conn = sqlite3.connect(str(path), check_same_thread=False)
            # WAL before anything else: it is what lets a reader (or a future
            # scheduler process) work alongside a writer. Persistent, so this
            # re-asserts rather than changes an existing file.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
            # Replaces the vector function Turso had built in; registered before
            # any query runs so semantic recall never sees it missing.
            conn.create_function(
                "vector_distance_cos", 2, _vector_distance_cos, deterministic=True
            )
            conn.executescript(_SCHEMA)
            conn.commit()
            _check_version(conn)
        except StoreError:
            _safe_close(conn)
            raise
        except (sqlite3.Error, OSError) as exc:
            _safe_close(conn)
            raise StoreError(f"could not open database at {path}: {exc}") from exc
        _conn, _conn_path = conn, path
        return conn


def _check_version(conn: sqlite3.Connection) -> None:
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        conn.commit()
        return
    try:
        found = int(row[0])
    except (TypeError, ValueError) as exc:
        # A hand-edited / half-written meta row must degrade like any other
        # corruption, not crash startup with a raw ValueError.
        raise StoreError(f"unreadable schema_version {row[0]!r} in the database") from exc
    if found > SCHEMA_VERSION:
        raise StoreError(
            f"database schema v{found} is newer than this Vegapunk "
            f"(v{SCHEMA_VERSION}) — upgrade Vegapunk"
        )


def _safe_close(conn: sqlite3.Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except sqlite3.Error:
        pass


def close_connection() -> None:
    """Close and forget the process-wide connection (tests, shutdown)."""
    global _conn, _conn_path
    with _conn_lock:
        _safe_close(_conn)
        _conn, _conn_path = None, None


def query(sql: str, params: tuple = ()) -> list[tuple]:
    """Run a SELECT and return all rows. Wraps driver errors as ``StoreError``."""
    with _conn_lock:
        conn = get_connection()
        try:
            cur = conn.execute(sql, params) if params else conn.execute(sql)
            return cur.fetchall()
        except sqlite3.Error as exc:
            raise StoreError(
                f"query failed ({sql.split()[0] if sql.split() else '?'}): {exc}"
            ) from exc


def execute(sql: str, params: tuple = ()) -> None:
    """Run a single write statement and commit. Wraps driver errors as ``StoreError``."""
    with _conn_lock:
        conn = get_connection()
        try:
            if params:
                conn.execute(sql, params)
            else:
                conn.execute(sql)
            conn.commit()
        except sqlite3.Error as exc:
            raise StoreError(
                f"write failed ({sql.split()[0] if sql.split() else '?'}): {exc}"
            ) from exc


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Group multiple writes into one commit.

    Commits on clean exit; rolls back and re-raises on failure (driver errors as
    ``StoreError``, other exceptions unchanged). Used by the embedding backfill.

    Holds ``_conn_lock`` for the whole block: the caller writes straight to the
    yielded connection, so no other thread may touch it until the commit lands.
    """
    with _conn_lock:
        conn = get_connection()
        try:
            yield conn
        except sqlite3.Error as exc:
            _safe_rollback(conn)
            raise StoreError(f"transaction failed: {exc}") from exc
        except BaseException:
            _safe_rollback(conn)
            raise
        else:
            try:
                conn.commit()
            except sqlite3.Error as exc:
                raise StoreError(f"commit failed: {exc}") from exc


def _safe_rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.rollback()
    except sqlite3.Error:
        pass


_lock_fd: int | None = None


def acquire_process_lock() -> None:
    """Take an exclusive advisory lock so only one Vegapunk uses the db at a time.

    A deliberate policy, no longer a driver limit: WAL admits concurrent processes
    safely, but nothing in Vegapunk yet *coordinates* two of them (two REPLs would
    both autosave the same conversation slug and both run the same due task), so
    the second one is still turned away. Lifting this is the scheduler-worker
    split's job, which needs one specific extra process rather than any number.

    The lock fd is held for the process lifetime — the kernel releases it on exit
    or crash, so there is no stale lock to clean up. Exits the process with a
    friendly message on contention. No-op (with a note) where ``fcntl`` is absent.
    """
    global _lock_fd
    if fcntl is None:
        print(
            "  [db] file locking unavailable on this platform — single-process guard off",
            file=sys.stderr,
        )
        return
    lock_path = Path(str(db_path()) + ".lock")
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as exc:
        print(f"  [db] could not open lock file {lock_path}: {exc}", file=sys.stderr)
        return
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        print(
            f"another Vegapunk is already using {db_path()} — run one at a time",
            file=sys.stderr,
        )
        raise SystemExit(1)
    _lock_fd = fd


def backup_now() -> Path:
    """Snapshot the database to a timestamped file under ``backups/`` and return it.

    Uses ``VACUUM INTO`` rather than the driver's ``Connection.backup``: it
    consolidates the WAL and compacts free pages, so the snapshot is one clean,
    minimal, ``sqlite3``-readable file. Raises ``StoreError`` on failure.

    Must not be called from inside a ``transaction()`` block: ``VACUUM INTO`` fails
    with an open write transaction on the shared connection.
    """
    with _conn_lock:
        conn = get_connection()
        backups_dir = db_path().parent / "backups"
        try:
            backups_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StoreError(f"could not create backups dir {backups_dir}: {exc}") from exc
        dest = backups_dir / f"vegapunk-{datetime.now():%Y%m%d-%H%M%S-%f}.db"
        # The directory part of dest is user-controlled (VEGAPUNK_DB_FILE / cwd may
        # contain a quote), so escape per SQL string-literal rules.
        escaped = str(dest).replace("'", "''")
        try:
            conn.execute(f"VACUUM INTO '{escaped}'")
        except sqlite3.Error as exc:
            raise StoreError(f"backup failed: {exc}") from exc
        return dest


def backup_if_stale(max_age_hours: int = 24, keep: int = 3) -> None:
    """Snapshot if the newest backup is older than ``max_age_hours`` (or none
    exists), then prune to the newest ``keep``. Entirely best-effort — any failure
    degrades to a stderr note; backups must never block the REPL."""
    backups_dir = db_path().parent / "backups"
    try:
        existing = sorted(backups_dir.glob("vegapunk-*.db"))
        newest_mtime = max((p.stat().st_mtime for p in existing), default=0.0)
        if time.time() - newest_mtime > max_age_hours * 3600:
            backup_now()
        if keep > 0:
            existing = sorted(backups_dir.glob("vegapunk-*.db"))
            for old in existing[:-keep]:
                old.unlink(missing_ok=True)
    except (StoreError, OSError) as exc:
        print(f"  [db] backup skipped: {exc}", file=sys.stderr)

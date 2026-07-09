"""The embedded Turso database — one file for sessions, memory, and input history.

Every other module talks to the database *only* through this module's turso-free
surface (``query``/``execute``/``transaction`` plus the small helpers), so the
``import turso`` line and the driver's exception type live in exactly one place.
That keeps a beta dependency swappable and lets callers handle failures through a
single ``StoreError`` (which subclasses ``OSError`` so existing best-effort
handlers keep working).

Single process at a time: Turso does not support multi-process access to one file,
so ``acquire_process_lock`` takes an advisory lock at startup. The file is a
standard SQLite database in WAL mode — any ``sqlite3`` client can read it (or a
``/backup`` snapshot) when Vegapunk is not running, which is the recovery path if
the beta driver ever misbehaves.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import turso

try:
    import fcntl
except ImportError:  # non-Unix; the single-process guard becomes a no-op with a note
    fcntl = None  # type: ignore[assignment]

from .config import config

SCHEMA_VERSION = 1

# Design rationale for these tables lives in the migration plan (§3). Kept free of
# SQL comments so ``executescript`` stays maximally portable across the driver.
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


def utcnow() -> str:
    """ISO-8601 UTC with microseconds, e.g. ``2026-07-09T12:34:56.123456Z``.

    Fixed width and lexicographically sortable; the microseconds keep same-second
    rows ordered without a separate sequence column.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_id() -> str:
    """A fresh opaque identifier (uuid4 hex) — no rowid dependence, so rows stay
    stable under a future Turso Cloud sync."""
    return uuid.uuid4().hex


_conn: turso.Connection | None = None
_conn_path: Path | None = None


def get_connection() -> turso.Connection:
    """Return the process-wide connection, opening + bootstrapping it on first use.

    Keyed on ``db_path()``: when the seam changes (how tests get isolation), the
    old connection is closed and a new one opened. Raises ``StoreError`` if the
    file can't be opened or the on-disk schema is newer than this code.
    """
    global _conn, _conn_path
    path = db_path()
    if _conn is not None and _conn_path == path:
        return _conn
    close_connection()
    conn: turso.Connection | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = turso.connect(str(path))
        conn.executescript(_SCHEMA)
        conn.commit()
        _check_version(conn)
    except StoreError:
        _safe_close(conn)
        raise
    except (turso.Error, OSError) as exc:
        _safe_close(conn)
        raise StoreError(f"could not open database at {path}: {exc}") from exc
    _conn, _conn_path = conn, path
    return conn


def _check_version(conn: turso.Connection) -> None:
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


def _safe_close(conn: turso.Connection | None) -> None:
    if conn is None:
        return
    try:
        conn.close()
    except turso.Error:
        pass


def close_connection() -> None:
    """Close and forget the process-wide connection (tests, shutdown)."""
    global _conn, _conn_path
    _safe_close(_conn)
    _conn, _conn_path = None, None


def query(sql: str, params: tuple = ()) -> list[tuple]:
    """Run a SELECT and return all rows. Wraps driver errors as ``StoreError``."""
    conn = get_connection()
    try:
        cur = conn.execute(sql, params) if params else conn.execute(sql)
        return cur.fetchall()
    except turso.Error as exc:
        raise StoreError(f"query failed ({sql.split()[0] if sql.split() else '?'}): {exc}") from exc


def execute(sql: str, params: tuple = ()) -> None:
    """Run a single write statement and commit. Wraps driver errors as ``StoreError``."""
    conn = get_connection()
    try:
        if params:
            conn.execute(sql, params)
        else:
            conn.execute(sql)
        conn.commit()
    except turso.Error as exc:
        raise StoreError(f"write failed ({sql.split()[0] if sql.split() else '?'}): {exc}") from exc


@contextmanager
def transaction() -> Iterator[turso.Connection]:
    """Group multiple writes into one commit.

    Commits on clean exit; rolls back and re-raises on failure (driver errors as
    ``StoreError``, other exceptions unchanged). Used by the migration importer
    and the embedding backfill.
    """
    conn = get_connection()
    try:
        yield conn
    except turso.Error as exc:
        _safe_rollback(conn)
        raise StoreError(f"transaction failed: {exc}") from exc
    except BaseException:
        _safe_rollback(conn)
        raise
    else:
        try:
            conn.commit()
        except turso.Error as exc:
            raise StoreError(f"commit failed: {exc}") from exc


def _safe_rollback(conn: turso.Connection) -> None:
    try:
        conn.rollback()
    except turso.Error:
        pass


_lock_fd: int | None = None


def acquire_process_lock() -> None:
    """Take an exclusive advisory lock so only one Vegapunk uses the db at a time.

    Turso does not support multi-process access; a second writer can corrupt the
    WAL. The lock fd is held for the process lifetime — the kernel releases it on
    exit or crash, so there is no stale lock to clean up. Exits the process with a
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

    Uses ``VACUUM INTO`` (there is no backup API), which also consolidates the WAL
    into one clean, ``sqlite3``-readable file. Raises ``StoreError`` on failure.

    Must not be called from inside a ``transaction()`` block: ``VACUUM INTO`` fails
    with an open write transaction on the shared connection.
    """
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
    except turso.Error as exc:
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

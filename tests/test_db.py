"""Tests for the embedded database core — bootstrap, the connection seam, the
error type, the single-process lock, and backups.

The autouse ``_isolated_vegapunk_home`` fixture (conftest) points ``db.db_path``
at a per-test tmp file and closes the connection on teardown, so these tests
never touch the developer's real ``.vegapunk/``.
"""

from __future__ import annotations

import os
import sqlite3
import struct
import subprocess
import sys
import threading
import time

import pytest

from vegapunk import db

fcntl = pytest.importorskip("fcntl", reason="single-process lock needs fcntl (Unix)")


def _table_names() -> set[str]:
    return {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_store_error_is_os_error():
    # The whole degrade-not-crash posture relies on this subclassing.
    assert issubclass(db.StoreError, OSError)


def test_bootstrap_creates_tables_and_version():
    assert _table_names() >= {"meta", "sessions", "memory", "input_history"}
    row = db.query("SELECT value FROM meta WHERE key = 'schema_version'")
    assert row == [(str(db.SCHEMA_VERSION),)]


def test_bootstrap_is_idempotent():
    first = db.get_connection()
    second = db.get_connection()
    assert first is second  # same process-wide singleton, not re-bootstrapped
    # schema_version row written exactly once
    assert db.query("SELECT count(*) FROM meta WHERE key = 'schema_version'") == [(1,)]


def test_newer_schema_version_refuses():
    db.get_connection()  # bootstrap at the current version
    db.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (str(db.SCHEMA_VERSION + 1),))
    db.close_connection()  # force a reopen so the version gate runs again
    with pytest.raises(db.StoreError, match="newer than this Vegapunk"):
        db.get_connection()


def test_malformed_schema_version_degrades_to_store_error():
    db.get_connection()  # bootstrap
    db.execute("UPDATE meta SET value = 'garbage' WHERE key = 'schema_version'")
    db.close_connection()  # force the version gate to re-run
    with pytest.raises(db.StoreError, match="unreadable schema_version"):
        db.get_connection()


def test_connection_follows_db_path_seam(tmp_path, monkeypatch):
    db.execute("INSERT INTO sessions (slug, messages, turns, created_at, updated_at) VALUES (?,?,?,?,?)",
               ("a", "[]", 0, "t", "t"))
    assert db.query("SELECT count(*) FROM sessions") == [(1,)]

    other = tmp_path / "other.db"
    monkeypatch.setattr("vegapunk.db.db_path", lambda: other)
    # Reconnects to the fresh file, which has its own (empty) sessions table.
    assert db.query("SELECT count(*) FROM sessions") == [(0,)]
    assert other.is_file()


def test_busy_timeout_is_set():
    # Not tuning: with multi-process access enabled and no busy timeout, two
    # processes writing at once lose most of their statements to "database is
    # locked" instead of queueing behind each other.
    assert db.query("PRAGMA busy_timeout") == [(db._BUSY_TIMEOUT_MS,)]


def test_multiprocess_wal_is_enabled(tmp_path, monkeypatch):
    # The capability the scheduler-worker split rests on: a SECOND OS PROCESS can
    # open and write this database while this one holds it open. Asserted through
    # behavior rather than by reading _EXPERIMENTAL_FEATURES, because the driver
    # silently ignores unknown feature names — a typo there would otherwise leave
    # us in single-process mode with a green test. The child reaches the same file
    # via VEGAPUNK_DB_FILE, the way a real worker process would.
    dbfile = tmp_path / "shared.db"
    monkeypatch.setattr("vegapunk.db.db_path", lambda: dbfile)
    db.get_connection()  # held open for the duration of the child's write

    child = (
        "from vegapunk import db; "
        "db.execute(\"INSERT INTO input_history (entry, created_at) "
        "VALUES ('from-child', 't')\")"
    )
    done = subprocess.run(
        [sys.executable, "-c", child],
        env={**os.environ, "VEGAPUNK_DB_FILE": str(dbfile)},
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert done.returncode == 0, f"child process failed:\n{done.stdout}\n{done.stderr}"
    # The parent reads, on its own live connection, what another process wrote.
    assert db.query("SELECT entry FROM input_history WHERE entry = 'from-child'") == [
        ("from-child",)
    ]


def test_each_thread_gets_its_own_connection():
    # The guarantee that keeps pyturso 0.7.1 from aborting the process: threads
    # never share a connection object. Asserted on identity, not behavior, because
    # a shared connection appears to work right up until it panics.
    # The connection objects are kept, not their ids: a finished thread's
    # connection is freed and the next one can be allocated at the same address,
    # which would make an id-only check pass for the wrong reason.
    conns: list = []
    guard = threading.Lock()
    started = threading.Barrier(6)

    def grab() -> None:
        started.wait(timeout=30)  # all six alive at once, so none can be recycled
        conn = db.get_connection()
        with guard:
            conns.append(conn)

    threads = [threading.Thread(target=grab) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert len(conns) == 6
    assert len({id(c) for c in conns}) == 6  # six threads, six distinct connections
    assert all(db.get_connection() is not c for c in conns)  # this thread's is its own


def test_repeated_calls_reuse_one_connection_per_thread():
    # Per-thread must not become per-call, or every query would pay a fresh open.
    assert db.get_connection() is db.get_connection()


def test_threads_can_read_and_write_concurrently():
    # Holding their own connections, threads now contend at the WAL level rather
    # than on a Python lock — which is exactly why _BUSY_TIMEOUT_MS is mandatory.
    # Without it, eight concurrent writers lose the large majority of their
    # statements to "database is locked".
    errors: list[Exception] = []

    def writer(n: int) -> None:
        try:
            for i in range(60):
                db.execute(
                    "INSERT INTO input_history (entry, created_at) VALUES (?, ?)",
                    (f"t{n}-{i}", db.utcnow()),
                )
                db.query("SELECT count(*) FROM input_history")
        except BaseException as exc:  # noqa: BLE001 — a Rust panic arrives as BaseException
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == []
    assert db.query("SELECT count(*) FROM input_history") == [(480,)]  # every write landed


def test_process_lock_refuses_second_holder(tmp_path, monkeypatch):
    dbfile = tmp_path / "locked.db"
    monkeypatch.setattr("vegapunk.db.db_path", lambda: dbfile)
    lock_path = str(dbfile) + ".lock"
    # Simulate another live Vegapunk holding the exclusive lock.
    held = open(lock_path, "w")
    try:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit):
            db.acquire_process_lock()
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()


def test_scheduler_lock_refuses_a_second_worker(tmp_path, monkeypatch):
    dbfile = tmp_path / "locked.db"
    monkeypatch.setattr("vegapunk.db.db_path", lambda: dbfile)
    held = open(str(dbfile) + ".scheduler.lock", "w")
    try:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(SystemExit):
            db.acquire_scheduler_lock()
    finally:
        fcntl.flock(held.fileno(), fcntl.LOCK_UN)
        held.close()


def test_repl_and_scheduler_locks_are_independent(tmp_path, monkeypatch):
    # The design rests on this: the worker runs *alongside* the REPL, so a held
    # REPL lock must not turn the worker away, or the scheduler could never start.
    dbfile = tmp_path / "both.db"
    monkeypatch.setattr("vegapunk.db.db_path", lambda: dbfile)
    repl_lock = open(str(dbfile) + ".lock", "w")
    try:
        fcntl.flock(repl_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # a REPL is running

        db.acquire_scheduler_lock()  # the worker still starts — different file

        with pytest.raises(SystemExit):
            db.acquire_process_lock()  # but a second REPL is still refused
    finally:
        fcntl.flock(repl_lock.fileno(), fcntl.LOCK_UN)
        repl_lock.close()


def test_backup_now_creates_readable_snapshot():
    # Store a row (with a vector blob) then snapshot it.
    vec = struct.pack("<4f", 1.0, 0.0, 0.0, 0.0)
    db.execute(
        "INSERT INTO memory (id, kind, content, created_at, updated_at, embedding) VALUES (?,?,?,?,?,?)",
        ("id1", "fact", "hello", "t", "t", vec),
    )
    dest = db.backup_now()
    assert dest.is_file()
    # Escape hatch: read the SNAPSHOT (a separate file with an independent lock)
    # with stdlib sqlite3 — no pyturso needed to recover data.
    snap = sqlite3.connect(str(dest))
    try:
        assert snap.execute("SELECT content FROM memory WHERE id = 'id1'").fetchone() == ("hello",)
        blob = snap.execute("SELECT embedding FROM memory WHERE id = 'id1'").fetchone()[0]
        assert struct.unpack("<4f", blob) == (1.0, 0.0, 0.0, 0.0)
    finally:
        snap.close()


def test_backup_if_stale_prunes_to_keep(tmp_path, monkeypatch):
    dbfile = tmp_path / "vegapunk.db"
    monkeypatch.setattr("vegapunk.db.db_path", lambda: dbfile)
    db.get_connection()  # bootstrap
    backups = dbfile.parent / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    # Seed pre-aged dummy snapshots (old mtime) rather than looping backup_now.
    for i in range(5):
        p = backups / f"vegapunk-2020010{i}-000000-000000.db"
        p.write_bytes(b"")
        old = time.time() - 10 * 24 * 3600
        os.utime(p, (old, old))

    db.backup_if_stale(max_age_hours=24, keep=3)

    remaining = sorted(p.name for p in backups.glob("vegapunk-*.db"))
    assert len(remaining) == 3  # pruned to the newest 3
    # The two newest dummies survive; the three oldest are gone. The third
    # survivor is the fresh stale-triggered backup (a 2026-dated name sorts last).
    assert "vegapunk-20200103-000000-000000.db" in remaining
    assert "vegapunk-20200104-000000-000000.db" in remaining
    assert "vegapunk-20200100-000000-000000.db" not in remaining
    assert remaining[-1].startswith("vegapunk-202")  # the newly created snapshot

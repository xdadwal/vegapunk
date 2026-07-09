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

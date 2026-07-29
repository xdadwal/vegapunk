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


def test_connection_opens_in_wal_mode():
    # WAL is what lets a reader — or a future scheduler process — work alongside
    # the REPL's writes. Losing it silently would strand that whole direction.
    assert db.query("PRAGMA journal_mode") == [("wal",)]


def test_busy_timeout_is_set():
    # Without it a writer meeting another writer's lock fails outright instead of
    # briefly queueing, which is how cross-process contention shows up.
    assert db.query("PRAGMA busy_timeout") == [(db._BUSY_TIMEOUT_MS,)]


def _vec(*values: float) -> bytes:
    return struct.pack(f"<{len(values)}f", *values)


def test_vector_distance_cos_is_registered_in_sql():
    # memory's semantic recall orders by this function in SQL. The stdlib driver
    # has no vector support, so db.py registers it — if that regresses, recall
    # degrades silently to text matching instead of failing loudly.
    (identical,) = db.query("SELECT vector_distance_cos(?, ?)", (_vec(1, 0, 0), _vec(1, 0, 0)))[0]
    (orthogonal,) = db.query("SELECT vector_distance_cos(?, ?)", (_vec(1, 0, 0), _vec(0, 1, 0)))[0]
    (opposed,) = db.query("SELECT vector_distance_cos(?, ?)", (_vec(1, 0, 0), _vec(-1, 0, 0)))[0]
    assert identical == pytest.approx(0.0)
    assert orthogonal == pytest.approx(1.0)
    assert opposed == pytest.approx(2.0)


@pytest.mark.parametrize(
    "a, b",
    [
        (_vec(1, 0, 0), _vec(1, 0)),  # dimension mismatch (embed model changed)
        (_vec(0, 0, 0), _vec(1, 0, 0)),  # zero vector — cosine undefined
        (None, _vec(1, 0, 0)),  # no embedding stored
        (b"\x01\x02", _vec(1, 0, 0)),  # not a whole number of float32s
    ],
)
def test_vector_distance_cos_ranks_unrankable_input_last(a, b):
    # A number past every real distance, not NULL: under ORDER BY distance a NULL
    # would sort such a row *first*, putting the least comparable fact at the top
    # of recall. It must also beat 2.0, which an exactly-opposed pair really scores.
    assert db.query("SELECT vector_distance_cos(?, ?)", (a, b)) == [(3.0,)]
    assert db._UNRANKABLE_DISTANCE > 2.0


def test_ordering_puts_unrankable_rows_behind_real_matches():
    # 'opposed' scores the worst *real* distance (2.0), so it pins that unrankable
    # rows sort behind even the least similar genuine match.
    for fact_id, vector in [
        ("near", _vec(1, 0, 0)),
        ("far", _vec(0, 1, 0)),
        ("opposed", _vec(-1, 0, 0)),
        ("bad", _vec(1, 0)),
    ]:
        db.execute(
            "INSERT INTO memory (id, kind, content, created_at, updated_at, embedding) "
            "VALUES (?,?,?,?,?,?)",
            (fact_id, "fact", fact_id, "t", "t", vector),
        )

    rows = db.query(
        "SELECT id FROM memory WHERE embedding IS NOT NULL "
        "ORDER BY vector_distance_cos(embedding, ?)",
        (_vec(1, 0, 0),),
    )

    assert [r[0] for r in rows] == ["near", "far", "opposed", "bad"]


def test_connection_is_safe_to_share_across_threads():
    # db.py owns the serialization: sqlite3.threadsafety is 1 on a stock build, so
    # one connection must not be used by two threads at once — and two do reach it,
    # the scheduler's ticker mid-task and the main thread writing input history at
    # the prompt, the latter outside the lock the CLI wraps its turns in.
    errors: list[Exception] = []

    def writer(n: int) -> None:
        try:
            for i in range(20):
                db.execute(
                    "INSERT INTO input_history (entry, created_at) VALUES (?, ?)",
                    (f"t{n}-{i}", db.utcnow()),
                )
                db.query("SELECT count(*) FROM input_history")
        except Exception as exc:  # noqa: BLE001 — the test's job is to report any failure
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert errors == []
    assert db.query("SELECT count(*) FROM input_history") == [(120,)]  # every write landed


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
    # Escape hatch: a snapshot is a plain SQLite file any client can recover from.
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

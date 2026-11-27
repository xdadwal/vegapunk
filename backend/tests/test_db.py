import sqlite3

import pytest

from vegapunk.config import Settings
from vegapunk.db import (
    DataDirError,
    SchemaTooNew,
    _migration_files,
    connect,
    migrate,
    prepare_data_dir,
)

LATEST = 6


def test_migrate_creates_tables_and_is_repeatable(tmp_path):
    conn = connect(tmp_path / "v.db")
    assert migrate(conn) == LATEST
    assert migrate(conn) == LATEST
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "admin",
        "sessions",
        "sign_in_failures",
        "host_metrics",
        "containers",
        "ai_settings",
        "conversations",
    } <= names


def test_refuses_database_from_newer_version(tmp_path):
    conn = connect(tmp_path / "v.db")
    conn.execute("PRAGMA user_version = 999")
    with pytest.raises(SchemaTooNew):
        migrate(conn)


def test_unwritable_data_dir_names_the_path(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(DataDirError, match=str(blocker)):
        prepare_data_dir(Settings(data_dir=blocker / "sub", static_dir=None, secure_cookies=False))


def test_foreign_keys_are_on(tmp_path):
    assert connect(tmp_path / "v.db").execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_startup_fails_on_newer_schema(settings):
    from fastapi.testclient import TestClient

    from vegapunk.app import create_app

    settings.data_dir.mkdir(parents=True)
    conn = connect(settings.db_path)
    conn.execute("PRAGMA user_version = 999")
    conn.close()
    with pytest.raises(SchemaTooNew), TestClient(create_app(settings)):
        pass


def test_0005_adds_the_watch_switch_on_to_a_database_at_0004(tmp_path):
    conn = connect(tmp_path / "v.db")
    for number, sql in _migration_files():
        if number <= 4:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    conn.execute("UPDATE check_settings SET enabled = 0, hour = 7, last_error = 'x' WHERE id = 1")
    conn.execute(
        "INSERT INTO findings (key, subject, title, diagnosis, suggested_fix, status, trigger, "
        "first_seen_at, last_seen_at) VALUES ('k', 'web', 't', 'd', 'f', 'open', 'watch', "
        "'2026-11-27T00:00:00Z', '2026-11-27T00:00:00Z')"
    )
    assert migrate(conn) == LATEST
    row = conn.execute("SELECT * FROM check_settings WHERE id = 1").fetchone()
    assert (row["enabled"], row["hour"], row["last_error"], row["watch_enabled"]) == (0, 7, "x", 1)
    assert [r["key"] for r in conn.execute("SELECT key FROM findings")] == ["k"]
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE check_settings SET watch_enabled = 2 WHERE id = 1")


def _at(conn, version):
    for number, sql in _migration_files():
        if number <= version:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")


def _finding(conn, key="k"):
    return conn.execute(
        "INSERT INTO findings (key, subject, title, diagnosis, suggested_fix, status, trigger, "
        "first_seen_at, last_seen_at) VALUES (?, 'web', 't', 'd', 'f', 'open', 'watch', "
        "'2026-11-27T00:00:00Z', '2026-11-27T00:00:00Z')",
        (key,),
    ).lastrowid


def test_0006_links_conversations_to_findings_on_a_database_at_0005(tmp_path):
    conn = connect(tmp_path / "v.db")
    _at(conn, 5)
    conn.execute(
        "INSERT INTO conversations (id, title, created_at, updated_at) "
        "VALUES ('c1', 'Old one', '2026-11-01T00:00:00Z', '2026-11-02T00:00:00Z')"
    )
    fid = _finding(conn)
    assert migrate(conn) == LATEST
    row = conn.execute("SELECT * FROM conversations WHERE id = 'c1'").fetchone()
    assert (row["title"], row["updated_at"], row["finding_id"]) == (
        "Old one",
        "2026-11-02T00:00:00Z",
        None,
    )

    conn.execute("UPDATE conversations SET finding_id = ? WHERE id = 'c1'", (fid,))
    with pytest.raises(sqlite3.IntegrityError):  # at most one conversation per finding
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at, finding_id) "
            "VALUES ('c2', 'x', 'a', 'a', ?)",
            (fid,),
        )
    # many conversations may have no finding
    for cid in ("c3", "c4"):
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) "
            "VALUES (?, 'x', 'a', 'a')",
            (cid,),
        )
    with pytest.raises(sqlite3.IntegrityError):  # the finding must exist
        conn.execute("UPDATE conversations SET finding_id = 999 WHERE id = 'c3'")

    conn.execute("DELETE FROM findings WHERE id = ?", (fid,))
    row = conn.execute("SELECT * FROM conversations WHERE id = 'c1'").fetchone()
    assert row is not None and row["finding_id"] is None

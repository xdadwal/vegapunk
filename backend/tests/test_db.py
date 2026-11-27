import pytest

from vegapunk.config import Settings
from vegapunk.db import DataDirError, SchemaTooNew, connect, migrate, prepare_data_dir

LATEST = 2


def test_migrate_creates_tables_and_is_repeatable(tmp_path):
    conn = connect(tmp_path / "v.db")
    assert migrate(conn) == LATEST
    assert migrate(conn) == LATEST
    names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"admin", "sessions", "sign_in_failures", "host_metrics", "containers"} <= names


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

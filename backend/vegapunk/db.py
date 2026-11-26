import sqlite3
from collections.abc import Iterator
from importlib import resources
from pathlib import Path

from fastapi import Request

from vegapunk.config import Settings


class SchemaTooNew(RuntimeError):
    pass


class DataDirError(RuntimeError):
    pass


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _migration_files() -> list[tuple[int, str]]:
    files = []
    for entry in resources.files("vegapunk").joinpath("migrations").iterdir():
        if entry.name.endswith(".sql"):
            files.append((int(entry.name.split("_", 1)[0]), entry.read_text()))
    return sorted(files)


def migrate(conn: sqlite3.Connection) -> int:
    files = _migration_files()
    latest = files[-1][0] if files else 0
    current = conn.execute("PRAGMA user_version").fetchone()[0]
    if current > latest:
        raise SchemaTooNew(
            f"The database is at version {current}, but this Vegapunk only knows version "
            f"{latest}. It was written by a newer Vegapunk; upgrade instead of downgrading."
        )
    for number, sql in files:
        if number > current:
            # executescript commits on its own, so wrap each file in an explicit transaction.
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
    return latest


def prepare_data_dir(settings: Settings) -> None:
    path = settings.data_dir
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.write_text("")
        probe.unlink()
    except OSError as exc:
        raise DataDirError(f"Can't use the data directory {path}: {exc.strerror}.") from exc


def get_db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = connect(request.app.state.settings.db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

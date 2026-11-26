import sqlite3
from dataclasses import dataclass
from datetime import datetime

from vegapunk.auth.passwords import hash_password

USERNAME_PATTERN = r"^[a-z0-9_-]{1,32}$"
MIN_PASSWORD_LENGTH = 10


class AdminExists(Exception):
    pass


@dataclass(frozen=True)
class Admin:
    username: str
    display_name: str
    password_hash: str
    created_at: datetime


def _from_row(row: sqlite3.Row) -> Admin:
    return Admin(
        username=row["username"],
        display_name=row["display_name"],
        password_hash=row["password_hash"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def get_admin(conn: sqlite3.Connection) -> Admin | None:
    row = conn.execute("SELECT * FROM admin WHERE id = 1").fetchone()
    return _from_row(row) if row else None


def create_admin(
    conn: sqlite3.Connection, *, username: str, display_name: str, password: str, now: datetime
) -> Admin:
    try:
        conn.execute(
            "INSERT INTO admin (id, username, display_name, password_hash, created_at) "
            "VALUES (1, ?, ?, ?, ?)",
            (username, display_name, hash_password(password), now.isoformat()),
        )
    except sqlite3.IntegrityError as exc:
        raise AdminExists from exc
    admin = get_admin(conn)
    assert admin is not None
    return admin

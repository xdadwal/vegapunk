import hashlib
import hmac
import secrets
import sqlite3
from datetime import datetime, timedelta

SESSION_COOKIE = "vegapunk_session"
SESSION_LIFETIME = timedelta(days=30)
MAX_FAILURES = 5
LOCKOUT = timedelta(minutes=15)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(conn: sqlite3.Connection, now: datetime) -> str:
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now.isoformat(),))
    token = secrets.token_urlsafe(32)
    conn.execute(
        "INSERT INTO sessions (token_hash, created_at, expires_at) VALUES (?, ?, ?)",
        (_digest(token), now.isoformat(), (now + SESSION_LIFETIME).isoformat()),
    )
    return token


def session_is_valid(conn: sqlite3.Connection, token: str, now: datetime) -> bool:
    row = conn.execute(
        "SELECT token_hash, expires_at FROM sessions WHERE token_hash = ?", (_digest(token),)
    ).fetchone()
    if row is None or not hmac.compare_digest(row["token_hash"], _digest(token)):
        return False
    return datetime.fromisoformat(row["expires_at"]) > now


def delete_session(conn: sqlite3.Connection, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_digest(token),))


def delete_all_sessions(conn: sqlite3.Connection, *, except_token: str | None = None) -> None:
    keep = _digest(except_token) if except_token else ""
    conn.execute("DELETE FROM sessions WHERE token_hash != ?", (keep,))


def record_failure(conn: sqlite3.Connection, now: datetime) -> None:
    conn.execute(
        "DELETE FROM sign_in_failures WHERE failed_at <= ?", ((now - LOCKOUT).isoformat(),)
    )
    conn.execute("INSERT INTO sign_in_failures (failed_at) VALUES (?)", (now.isoformat(),))


def clear_failures(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM sign_in_failures")


def locked_until(conn: sqlite3.Connection, now: datetime) -> datetime | None:
    rows = conn.execute(
        "SELECT failed_at FROM sign_in_failures WHERE failed_at > ? "
        "ORDER BY failed_at DESC LIMIT ?",
        ((now - LOCKOUT).isoformat(), MAX_FAILURES),
    ).fetchall()
    if len(rows) < MAX_FAILURES:
        return None
    return datetime.fromisoformat(rows[-1]["failed_at"]) + LOCKOUT

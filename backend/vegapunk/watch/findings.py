"""Findings, their evidence, check settings and investigation history, in SQLite."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from vegapunk.db import transaction
from vegapunk.timefmt import z

MAX_TITLE = 120
MAX_DIAGNOSIS = 1_200
MAX_FIX = 600
MAX_EVIDENCE_LINES = 20
MAX_EVIDENCE_TEXT = 300

_COLUMNS = (
    "id, key, subject, title, diagnosis, suggested_fix, status, trigger, "
    "first_seen_at, last_seen_at, resolved_at, reviewed_at"
)


@dataclass(frozen=True)
class EvidenceLine:
    source: Literal["log", "metric"]
    text: str
    at: str | None


@dataclass(frozen=True)
class FindingDraft:
    subject: str
    title: str
    diagnosis: str
    suggested_fix: str
    evidence: tuple[EvidenceLine, ...]


@dataclass(frozen=True)
class CheckSettings:
    enabled: bool  # the daily check
    hour: int
    watch_enabled: bool  # signal-triggered investigations


def upsert_finding(
    conn: sqlite3.Connection,
    draft: FindingDraft,
    *,
    key: str,
    trigger: Literal["scheduled", "watch"],
    now: datetime,
) -> int:
    """Update the open finding with this key, or insert one. Returns its id."""
    with transaction(conn):
        return _upsert(conn, draft, key=key, trigger=trigger, now=now)


def store_findings(
    conn: sqlite3.Connection,
    items: list[tuple[str, FindingDraft]],
    *,
    trigger: Literal["scheduled", "watch"],
    now: datetime,
) -> list[int]:
    """Upsert several (key, draft) pairs in one transaction: all are stored or none."""
    with transaction(conn):
        return [_upsert(conn, d, key=k, trigger=trigger, now=now) for k, d in items]


def _upsert(
    conn: sqlite3.Connection,
    draft: FindingDraft,
    *,
    key: str,
    trigger: Literal["scheduled", "watch"],
    now: datetime,
) -> int:
    stamp = z(now)
    fields = (
        draft.subject,
        draft.title[:MAX_TITLE],
        draft.diagnosis[:MAX_DIAGNOSIS],
        draft.suggested_fix[:MAX_FIX],
    )
    row = conn.execute(
        "SELECT id FROM findings WHERE key = ? AND status = 'open'", (key,)
    ).fetchone()
    if row:
        finding_id = row["id"]
        conn.execute(
            "UPDATE findings SET subject = ?, title = ?, diagnosis = ?, suggested_fix = ?, "
            "trigger = ?, last_seen_at = ? WHERE id = ?",
            (*fields, trigger, stamp, finding_id),
        )
        conn.execute("DELETE FROM finding_evidence WHERE finding_id = ?", (finding_id,))
    else:
        finding_id = conn.execute(
            "INSERT INTO findings (key, subject, title, diagnosis, suggested_fix, status, "
            "trigger, first_seen_at, last_seen_at) VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)",
            (key, *fields, trigger, stamp, stamp),
        ).lastrowid
    conn.executemany(
        "INSERT INTO finding_evidence (finding_id, position, source, text, at) "
        "VALUES (?, ?, ?, ?, ?)",
        [
            (finding_id, i, e.source, e.text[:MAX_EVIDENCE_TEXT], e.at)
            for i, e in enumerate(draft.evidence[:MAX_EVIDENCE_LINES])
        ],
    )
    return finding_id


def list_findings(conn: sqlite3.Connection, *, status: str | None, query: str | None) -> list[dict]:
    where, args = [], []
    if status:
        where.append("status = ?")
        args.append(status)
    if query:
        escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(title LIKE ? ESCAPE '\\' OR subject LIKE ? ESCAPE '\\')")
        args += [f"%{escaped}%"] * 2
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = conn.execute(
        f"SELECT {_COLUMNS} FROM findings {clause} "
        "ORDER BY (status = 'open') DESC, last_seen_at DESC, id DESC",
        args,
    ).fetchall()
    return [dict(r) for r in rows]


def get_finding(conn: sqlite3.Connection, finding_id: int) -> dict | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM findings WHERE id = ?", (finding_id,)).fetchone()
    if row is None:
        return None
    evidence = conn.execute(
        "SELECT source, text, at FROM finding_evidence WHERE finding_id = ? ORDER BY position",
        (finding_id,),
    ).fetchall()
    return {**dict(row), "evidence": [dict(e) for e in evidence]}


def review(conn: sqlite3.Connection, finding_id: int, now: datetime) -> bool:
    """Mark an open finding reviewed. False if there is no such open finding."""
    with transaction(conn):
        cur = conn.execute(
            "UPDATE findings SET status = 'reviewed', reviewed_at = ? "
            "WHERE id = ? AND status = 'open'",
            (z(now), finding_id),
        )
    return cur.rowcount > 0


def resolve(conn: sqlite3.Connection, key: str, now: datetime) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE findings SET status = 'resolved', resolved_at = ? "
            "WHERE key = ? AND status = 'open'",
            (z(now), key),
        )


def open_keys(conn: sqlite3.Connection) -> set[str]:
    return {r["key"] for r in conn.execute("SELECT key FROM findings WHERE status = 'open'")}


def open_count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM findings WHERE status = 'open'").fetchone()[0]


def load_check_settings(conn: sqlite3.Connection) -> CheckSettings:
    row = conn.execute(
        "SELECT enabled, hour, watch_enabled FROM check_settings WHERE id = 1"
    ).fetchone()
    return CheckSettings(
        enabled=bool(row["enabled"]), hour=row["hour"], watch_enabled=bool(row["watch_enabled"])
    )


def save_check_settings(
    conn: sqlite3.Connection, enabled: bool, hour: int, watch_enabled: bool = True
) -> None:
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("Hour must be a whole number from 0 to 23.")
    if not isinstance(watch_enabled, bool):
        raise ValueError("The watch switch must be true or false.")
    with transaction(conn):
        conn.execute(
            "UPDATE check_settings SET enabled = ?, hour = ?, watch_enabled = ? WHERE id = 1",
            (int(enabled), hour, int(watch_enabled)),
        )


def record_investigation(
    conn: sqlite3.Connection,
    key: str,
    trigger: str,
    now: datetime,
    outcome: Literal["finding", "nothing", "discarded", "failed"],
    error: str | None = None,
    marker: str | None = None,
) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO investigations (key, trigger, started_at, outcome, error, marker) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (key, trigger, z(now), outcome, error, marker),
        )


def used_markers(conn: sqlite3.Connection, key: str) -> set[str]:
    rows = conn.execute(
        "SELECT marker FROM investigations WHERE key = ? AND marker IS NOT NULL", (key,)
    )
    return {r["marker"] for r in rows}


def investigation_counts(
    conn: sqlite3.Connection, key: str, now: datetime
) -> tuple[datetime | None, datetime | None, int]:
    """(last started, last failed, how many started in the last 24 hours) for a key."""
    last = conn.execute(
        "SELECT MAX(started_at) FROM investigations WHERE key = ?", (key,)
    ).fetchone()[0]
    failed = conn.execute(
        "SELECT MAX(started_at) FROM investigations WHERE key = ? AND outcome = 'failed'", (key,)
    ).fetchone()[0]
    count = conn.execute(
        "SELECT COUNT(*) FROM investigations WHERE key = ? AND started_at > ?",
        (key, z(now - timedelta(hours=24))),
    ).fetchone()[0]
    parse = lambda s: None if s is None else datetime.fromisoformat(s)  # noqa: E731
    return parse(last), parse(failed), count


INTERRUPTED = "The check was interrupted."


def begin_investigation(
    conn: sqlite3.Connection, key: str, trigger: str, now: datetime, marker: str | None = None
) -> int:
    """Record an attempt before the model is called, as failed until `finish_investigation`
    says otherwise, so a crash or a stop mid-run still counts toward the limits."""
    with transaction(conn):
        return conn.execute(
            "INSERT INTO investigations (key, trigger, started_at, outcome, error, marker) "
            "VALUES (?, ?, ?, 'failed', ?, ?)",
            (key, trigger, z(now), INTERRUPTED, marker),
        ).lastrowid


def finish_investigation(
    conn: sqlite3.Connection,
    investigation_id: int,
    outcome: Literal["finding", "nothing", "discarded", "failed"],
    error: str | None = None,
) -> None:
    with transaction(conn):
        conn.execute(
            "UPDATE investigations SET outcome = ?, error = ? WHERE id = ?",
            (outcome, error, investigation_id),
        )


def watch_count(conn: sqlite3.Connection, now: datetime) -> int:
    """How many watch investigations started in the last 24 hours, across all keys."""
    return conn.execute(
        "SELECT COUNT(*) FROM investigations WHERE trigger = 'watch' AND started_at > ?",
        (z(now - timedelta(hours=24)),),
    ).fetchone()[0]


def last_reviewed_at(conn: sqlite3.Connection, key: str) -> datetime | None:
    value = conn.execute(
        "SELECT MAX(reviewed_at) FROM findings WHERE key = ? AND status = 'reviewed'", (key,)
    ).fetchone()[0]
    return None if value is None else datetime.fromisoformat(value)


@dataclass(frozen=True)
class CheckState:
    enabled: bool
    hour: int
    watch_enabled: bool
    last_run_at: datetime | None
    last_error: str | None


def load_check_state(conn: sqlite3.Connection) -> CheckState:
    row = conn.execute(
        "SELECT enabled, hour, watch_enabled, last_run_at, last_error "
        "FROM check_settings WHERE id = 1"
    ).fetchone()
    last = row["last_run_at"]
    return CheckState(
        enabled=bool(row["enabled"]),
        hour=row["hour"],
        watch_enabled=bool(row["watch_enabled"]),
        last_run_at=datetime.fromisoformat(last) if last else None,
        last_error=row["last_error"],
    )


def set_check_last_run(conn: sqlite3.Connection, now: datetime) -> None:
    with transaction(conn):
        conn.execute("UPDATE check_settings SET last_run_at = ? WHERE id = 1", (z(now),))


def set_check_error(conn: sqlite3.Connection, error: str | None) -> None:
    with transaction(conn):
        conn.execute("UPDATE check_settings SET last_error = ? WHERE id = 1", (error,))

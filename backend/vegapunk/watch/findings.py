"""Findings, their evidence, check settings and investigation history, in SQLite."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from vegapunk.api.queries import z
from vegapunk.db import transaction

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
    enabled: bool
    hour: int


def upsert_finding(
    conn: sqlite3.Connection,
    draft: FindingDraft,
    *,
    key: str,
    trigger: Literal["scheduled", "watch"],
    now: datetime,
) -> int:
    """Update the open finding with this key, or insert one. Returns its id."""
    stamp = z(now)
    fields = (
        draft.subject,
        draft.title[:MAX_TITLE],
        draft.diagnosis[:MAX_DIAGNOSIS],
        draft.suggested_fix[:MAX_FIX],
    )
    with transaction(conn):
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
    row = conn.execute("SELECT enabled, hour FROM check_settings WHERE id = 1").fetchone()
    return CheckSettings(enabled=bool(row["enabled"]), hour=row["hour"])


def save_check_settings(conn: sqlite3.Connection, enabled: bool, hour: int) -> None:
    if isinstance(hour, bool) or not isinstance(hour, int) or not 0 <= hour <= 23:
        raise ValueError("Hour must be a whole number from 0 to 23.")
    with transaction(conn):
        conn.execute(
            "UPDATE check_settings SET enabled = ?, hour = ? WHERE id = 1", (int(enabled), hour)
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

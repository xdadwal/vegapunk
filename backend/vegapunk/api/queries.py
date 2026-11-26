"""Reads shared by the API routes."""

import sqlite3
from datetime import UTC, datetime

from vegapunk.capture.status import container_status

STALE_AFTER_INTERVALS = 3

# Newest raw sample per container, joined on.
_WITH_LATEST_SAMPLE = (
    "SELECT c.*, m.cpu_pct AS m_cpu, m.mem_used AS m_mem, m.mem_limit AS m_limit, m.ts AS m_ts "
    "FROM containers c LEFT JOIN container_metrics m ON m.container_id = c.id AND m.res = 0 "
    "AND m.ts = (SELECT MAX(ts) FROM container_metrics WHERE container_id = c.id AND res = 0) "
)

# A name can briefly belong to two live rows (an old container and its replacement). Only the
# most recently seen one counts.
_LIVE = (
    "WHERE c.removed_at IS NULL AND c.id = (SELECT c2.id FROM containers c2 "
    "WHERE c2.name = c.name AND c2.removed_at IS NULL "
    "ORDER BY c2.last_seen_at DESC, c2.first_seen_at DESC, c2.id DESC LIMIT 1)"
)


def z(value: datetime | str | None) -> str | None:
    """ISO-8601 UTC with a Z, from a datetime, an ISO string, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def from_ts(ts: int) -> str:
    return z(datetime.fromtimestamp(ts, UTC))  # type: ignore[return-value]


def item(row: sqlite3.Row, *, now: datetime, interval: int, removed: bool = False) -> dict:
    """One container as the API returns it. Current values are shown only while the newest
    sample is recent, and never for a removed container."""
    fresh = (
        not removed
        and row["m_ts"] is not None
        and row["m_ts"] >= now.timestamp() - STALE_AFTER_INTERVALS * interval
    )
    return {
        "id": row["id"],
        "name": row["name"],
        "image": row["image"],
        "state": row["state"],
        "health": row["health"],
        "status": "offline" if removed else container_status(row["state"], row["health"]),
        "removed": removed,
        "restart_count": row["restart_count"],
        "started_at": z(row["started_at"]),
        "cpu_pct": row["m_cpu"] if fresh else None,
        "mem_used": row["m_mem"] if fresh else None,
        "mem_limit": row["m_limit"] if fresh else None,
    }


def live_container_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(_WITH_LATEST_SAMPLE + _LIVE).fetchall()


def container_row_by_name(conn: sqlite3.Connection, name: str) -> tuple[sqlite3.Row, bool] | None:
    """The live container with this name, else the most recently seen removed one."""
    live = conn.execute(
        _WITH_LATEST_SAMPLE + "WHERE c.name = ? AND c.removed_at IS NULL "
        "ORDER BY c.last_seen_at DESC, c.first_seen_at DESC, c.id DESC LIMIT 1",
        (name,),
    ).fetchone()
    if live is not None:
        return live, False
    gone = conn.execute(
        _WITH_LATEST_SAMPLE + "WHERE c.name = ? AND c.removed_at IS NOT NULL "
        "ORDER BY c.last_seen_at DESC, c.id DESC LIMIT 1",
        (name,),
    ).fetchone()
    return (gone, True) if gone is not None else None

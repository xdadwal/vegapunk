"""What the API says about the host and its containers.

One place builds these, so the routes and the agent's tools can never disagree about
what the screens show.
"""

import re
import sqlite3
from datetime import UTC, datetime

from vegapunk.api.metrics import series
from vegapunk.api.queries import (
    STALE_AFTER_INTERVALS,
    container_row_by_name,
    from_ts,
    item,
    live_container_rows,
    z,
)
from vegapunk.capture.collectors import SOURCES

# Docker's own rule for container names. A "/" can never appear, which matters because an
# encoded slash is decoded before routing and could not be matched there anyway.
NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_ORDER = {"offline": 0, "degraded": 1, "unknown": 2, "online": 3}


def _sources(
    conn: sqlite3.Connection, *, interval: int, started_at: datetime, now: datetime
) -> dict:
    rows = {r["source"]: r for r in conn.execute("SELECT * FROM capture_status")}
    out = {}
    for name in SOURCES:
        row = rows.get(name)
        ok = row is not None and row["last_error"] is None
        last_success = row["last_success_at"] if row else None
        since = datetime.fromisoformat(last_success) if last_success else started_at
        out[name] = {
            "ok": ok,
            "stale": (now - since).total_seconds() > STALE_AFTER_INTERVALS * interval,
            "last_success_at": z(last_success),
            "error": row["last_error"] if row is not None and not ok else None,
        }
    return out


def host_payload(
    conn: sqlite3.Connection, *, interval: int, started_at: datetime, now: datetime
) -> dict:
    facts = conn.execute("SELECT * FROM host WHERE id = 1").fetchone()
    latest = conn.execute(
        "SELECT * FROM host_metrics WHERE res = 0 ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    statuses = [item(r, now=now, interval=interval)["status"] for r in live_container_rows(conn)]
    return {
        # The server's clock, so "4s ago" and uptime are right whatever the browser's says.
        "now": z(now),
        "hostname": facts["hostname"] if facts else None,
        "os": facts["os"] if facts else None,
        "kernel": facts["kernel"] if facts else None,
        "docker_version": facts["docker_version"] if facts else None,
        "cpu_count": facts["cpu_count"] if facts else None,
        "boot_time": z(facts["boot_time"]) if facts else None,
        "latest": None
        if latest is None
        else {
            "observed_at": from_ts(latest["ts"]),
            "cpu_pct": latest["cpu_pct"],
            "mem_used": latest["mem_used"],
            "mem_total": latest["mem_total"],
            "disk_used": latest["disk_used"],
            "disk_total": latest["disk_total"],
        },
        "containers": {
            "total": len(statuses),
            "running": statuses.count("online"),
            "needs_attention": statuses.count("degraded") + statuses.count("offline"),
        },
        "capture": {
            "interval_seconds": interval,
            "sources": _sources(conn, interval=interval, started_at=started_at, now=now),
        },
    }


def _pct(used, total) -> float | None:
    if used is None or not total:
        return None
    return round(used / total * 100, 1)


def host_metrics_payload(conn: sqlite3.Connection, *, range_: str, interval: int) -> dict:
    resolution, rows = series(
        conn,
        table="host_metrics",
        key_sql="1 = 1",
        key_params=(),
        columns=("cpu_pct", "mem_used", "mem_total", "disk_used", "disk_total"),
        range_=range_,
        raw_interval=interval,
    )
    return {
        "range": range_,
        "resolution_seconds": resolution,
        "points": [
            {
                "ts": r["ts"],
                "cpu_pct": None if r["cpu_pct"] is None else round(r["cpu_pct"], 1),
                "mem_pct": _pct(r["mem_used"], r["mem_total"]),
                "disk_pct": _pct(r["disk_used"], r["disk_total"]),
            }
            for r in rows
        ],
    }


def containers_payload(conn: sqlite3.Connection, *, interval: int, now: datetime) -> dict:
    items = [item(r, now=now, interval=interval) for r in live_container_rows(conn)]
    items.sort(key=lambda i: (_ORDER[i["status"]], i["name"]))
    return {"total": len(items), "items": items}


def container_payload(
    conn: sqlite3.Connection, name: str, *, range_: str, interval: int, now: datetime
) -> dict | None:
    """One container with its history, or None if the name is invalid or unknown."""
    found = container_row_by_name(conn, name) if NAME.match(name) else None
    if found is None:
        return None
    row, removed = found
    resolution, points = series(
        conn,
        table="container_metrics",
        key_sql="container_id = ?",
        key_params=(row["id"],),
        columns=("cpu_pct", "mem_used", "mem_limit"),
        range_=range_,
        raw_interval=interval,
    )
    return {
        **item(row, now=now, interval=interval, removed=removed),
        "now": z(now),  # the server's clock, for "started 3 h ago" and uptime
        "metrics": {"range": range_, "resolution_seconds": resolution, "points": points},
    }


def utcnow() -> datetime:
    return datetime.now(UTC)

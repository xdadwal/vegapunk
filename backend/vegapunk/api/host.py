from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from vegapunk.api.metrics import Range, series
from vegapunk.api.queries import STALE_AFTER_INTERVALS, from_ts, item, live_container_rows, z
from vegapunk.auth.admin import Admin
from vegapunk.auth.routes import Db, require_admin
from vegapunk.capture.collectors import SOURCES

router = APIRouter(prefix="/api")
Admin_ = Annotated[Admin, Depends(require_admin)]


def _sources(conn, request: Request, now: datetime) -> dict:
    interval = request.app.state.settings.capture_interval
    started_at = request.app.state.started_at
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


@router.get("/host")
def host(conn: Db, request: Request, _: Admin_) -> dict:
    now = datetime.now(UTC)
    interval = request.app.state.settings.capture_interval
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
        "capture": {"interval_seconds": interval, "sources": _sources(conn, request, now)},
    }


def _pct(used, total) -> float | None:
    if used is None or not total:
        return None
    return round(used / total * 100, 1)


@router.get("/host/metrics")
def host_metrics(conn: Db, request: Request, _: Admin_, range: Range = "1h") -> dict:
    resolution, rows = series(
        conn,
        table="host_metrics",
        key_sql="1 = 1",
        key_params=(),
        columns=("cpu_pct", "mem_used", "mem_total", "disk_used", "disk_total"),
        range_=range,
        raw_interval=request.app.state.settings.capture_interval,
    )
    return {
        "range": range,
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

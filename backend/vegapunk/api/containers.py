import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from vegapunk.api.metrics import Range, series
from vegapunk.api.queries import container_row_by_name, item, live_container_rows
from vegapunk.auth.admin import Admin
from vegapunk.auth.routes import Db, require_admin

router = APIRouter(prefix="/api")
Admin_ = Annotated[Admin, Depends(require_admin)]

# Docker's own rule for container names. A "/" can never appear, which matters because an
# encoded slash is decoded before routing and could not be matched here anyway.
NAME = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_ORDER = {"offline": 0, "degraded": 1, "unknown": 2, "online": 3}


@router.get("/containers")
def containers(conn: Db, request: Request, _: Admin_) -> dict:
    now = datetime.now(UTC)
    interval = request.app.state.settings.capture_interval
    items = [item(r, now=now, interval=interval) for r in live_container_rows(conn)]
    items.sort(key=lambda i: (_ORDER[i["status"]], i["name"]))
    return {"total": len(items), "items": items}


@router.get("/containers/{name}")
def container(conn: Db, request: Request, name: str, _: Admin_, range: Range = "1h") -> dict:
    found = container_row_by_name(conn, name) if NAME.match(name) else None
    if found is None:
        raise HTTPException(404, f"No container named {name}")
    row, removed = found
    settings = request.app.state.settings
    resolution, points = series(
        conn,
        table="container_metrics",
        key_sql="container_id = ?",
        key_params=(row["id"],),
        columns=("cpu_pct", "mem_used", "mem_limit"),
        range_=range,
        raw_interval=settings.capture_interval,
    )
    return {
        **item(row, now=datetime.now(UTC), interval=settings.capture_interval, removed=removed),
        "metrics": {"range": range, "resolution_seconds": resolution, "points": points},
    }

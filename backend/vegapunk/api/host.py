from typing import Annotated

from fastapi import APIRouter, Depends, Request

from vegapunk.api.metrics import Range
from vegapunk.api.payloads import host_metrics_payload, host_payload, utcnow
from vegapunk.auth.admin import Admin
from vegapunk.auth.routes import Db, require_admin

router = APIRouter(prefix="/api")
Admin_ = Annotated[Admin, Depends(require_admin)]


@router.get("/host")
def host(conn: Db, request: Request, _: Admin_) -> dict:
    return host_payload(
        conn,
        interval=request.app.state.settings.capture_interval,
        started_at=request.app.state.started_at,
        now=utcnow(),
    )


@router.get("/host/metrics")
def host_metrics(conn: Db, request: Request, _: Admin_, range: Range = "1h") -> dict:
    return host_metrics_payload(
        conn, range_=range, interval=request.app.state.settings.capture_interval
    )

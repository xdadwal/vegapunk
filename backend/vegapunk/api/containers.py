from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request

from vegapunk.api.metrics import Range
from vegapunk.api.payloads import container_payload, containers_payload, utcnow
from vegapunk.auth.admin import Admin
from vegapunk.auth.routes import Db, require_admin

router = APIRouter(prefix="/api")
Admin_ = Annotated[Admin, Depends(require_admin)]


@router.get("/containers")
def containers(conn: Db, request: Request, _: Admin_) -> dict:
    return containers_payload(
        conn, interval=request.app.state.settings.capture_interval, now=utcnow()
    )


@router.get("/containers/{name}")
def container(conn: Db, request: Request, name: str, _: Admin_, range: Range = "1h") -> dict:
    payload = container_payload(
        conn,
        name,
        range_=range,
        interval=request.app.state.settings.capture_interval,
        now=utcnow(),
    )
    if payload is None:
        raise HTTPException(404, f"No container named {name}")
    return payload

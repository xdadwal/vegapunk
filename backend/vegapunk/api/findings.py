from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, StrictBool, StrictInt

from vegapunk.api.queries import z
from vegapunk.auth.admin import Admin
from vegapunk.auth.routes import Db, require_admin
from vegapunk.watch import findings
from vegapunk.watch.runner import CHECK_KEY

router = APIRouter(prefix="/api/findings")
settings_router = APIRouter(prefix="/api/settings")
Admin_ = Annotated[Admin, Depends(require_admin)]


def require_app_header(request: Request) -> None:
    """These POSTs carry no JSON body, so SameSite=Lax alone would let a same-site page post
    a form to them. A custom header cannot be sent by a plain form or without CORS."""
    if request.headers.get("x-requested-with") != "vegapunk":
        raise HTTPException(403, "Missing request header.")


AppHeader = Depends(require_app_header)

NO_SUCH = "No such finding."
MAX_ID = 2**63 - 1

_ITEM_FIELDS = (
    "id key subject title status trigger first_seen_at last_seen_at reviewed_at resolved_at"
).split()


class ChecksIn(BaseModel):
    enabled: StrictBool
    hour: StrictInt


def _finding_id(raw: str) -> int:
    if len(raw) > 19 or not raw.isascii() or not raw.isdigit() or not 0 < int(raw) <= MAX_ID:
        raise HTTPException(404, NO_SUCH)
    return int(raw)


def _check_has_run(conn) -> bool:
    """A fresh install stores a placeholder last run time before any check ran."""
    row = conn.execute(
        "SELECT 1 FROM investigations WHERE key = ? LIMIT 1", (CHECK_KEY,)
    ).fetchone()
    return row is not None


def _checks(conn, runner_status: dict | None) -> dict:
    state = findings.load_check_state(conn)
    ran = _check_has_run(conn)
    if runner_status is None:
        return {
            "enabled": state.enabled,
            "hour": state.hour,
            "last_run_at": z(state.last_run_at) if ran else None,
            "last_error": state.last_error,
            "skipped": None,
            "running": False,
        }
    running = bool(runner_status["running"])
    return {
        "enabled": state.enabled,
        "hour": state.hour,
        "last_run_at": runner_status["last_run_at"] if ran else None,
        # The runner marks a run as interrupted when it starts; hide that during a run.
        "last_error": None if running else runner_status["last_error"],
        "skipped": runner_status["skipped"],
        "running": running,
    }


@router.get("")
async def list_all(
    conn: Db,
    request: Request,
    _: Admin_,
    status: Literal["open", "resolved", "reviewed"] | None = None,
    q: str | None = None,
) -> dict:
    runner = request.app.state.runner
    runner_status = await runner.status() if runner is not None else None
    return await run_in_threadpool(_list, conn, status, q, runner_status)


def _list(conn, status: str | None, q: str | None, runner_status: dict | None) -> dict:
    rows = findings.list_findings(conn, status=status, query=q)
    return {
        "items": [{k: r[k] for k in _ITEM_FIELDS} for r in rows],
        "open_count": findings.open_count(conn),
        "checks": _checks(conn, runner_status),
    }


_CONFLICTS = {
    "off": "Connect an AI provider first.",
    "key_rejected": "Your AI provider rejected the API key.",
    "unsupported": "Your AI provider can't give structured answers, so checks can't run.",
}


@router.post("/check", status_code=202)
async def check_now(request: Request, _: Admin_, __: None = AppHeader) -> dict:
    runner = request.app.state.runner
    if runner is None:
        raise HTTPException(409, "Checks aren't running on this server.")
    status = await runner.status()
    if status["skipped"] in _CONFLICTS:
        raise HTTPException(409, _CONFLICTS[status["skipped"]])
    if status["running"] or not await runner.run_check_now():
        raise HTTPException(409, "Another check is running. Try again in a moment.")
    return {"started": True}


@router.get("/{finding_id}")
def get_one(finding_id: str, conn: Db, _: Admin_) -> dict:
    found = findings.get_finding(conn, _finding_id(finding_id))
    if found is None:
        raise HTTPException(404, NO_SUCH)
    return found


@router.post("/{finding_id}/review", status_code=204)
def review_one(finding_id: str, conn: Db, _: Admin_, __: None = AppHeader) -> Response:
    fid = _finding_id(finding_id)
    if findings.get_finding(conn, fid) is None:
        raise HTTPException(404, NO_SUCH)
    findings.review(conn, fid, datetime.now(UTC))
    return Response(status_code=204)


@settings_router.get("/checks")
def get_checks(conn: Db, _: Admin_) -> dict:
    return _check_settings(conn)


def _check_settings(conn) -> dict:
    s = findings.load_check_settings(conn)
    return {"enabled": s.enabled, "hour": s.hour}


@settings_router.put("/checks")
def put_checks(body: ChecksIn, conn: Db, _: Admin_) -> dict:
    try:
        findings.save_check_settings(conn, body.enabled, body.hour)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return _check_settings(conn)

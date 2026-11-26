import sqlite3
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StringConstraints

from vegapunk.auth import admin as admin_store
from vegapunk.auth import sessions
from vegapunk.auth.admin import MIN_PASSWORD_LENGTH, USERNAME_PATTERN, Admin
from vegapunk.auth.passwords import hash_password, verify_password
from vegapunk.db import get_db

router = APIRouter(prefix="/api")

Db = Annotated[sqlite3.Connection, Depends(get_db)]

# Verified against when the username is unknown, so both failures take similar time.
_DUMMY_HASH = hash_password("not-a-real-password")


def _now() -> datetime:
    return datetime.now(UTC)


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    response.set_cookie(
        sessions.SESSION_COOKIE,
        token,
        max_age=int(sessions.SESSION_LIFETIME.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=request.app.state.settings.secure_cookies,
        path="/",
    )


def session_token(
    token: Annotated[str | None, Cookie(alias=sessions.SESSION_COOKIE)] = None,
) -> str | None:
    return token


def require_admin(conn: Db, token: Annotated[str | None, Depends(session_token)]) -> Admin:
    admin = admin_store.get_admin(conn)
    if admin is None or not token or not sessions.session_is_valid(conn, token, _now()):
        raise HTTPException(401, "Not signed in.")
    return admin


DisplayName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]


class ProfileIn(BaseModel):
    username: str | None = Field(default=None, pattern=USERNAME_PATTERN)
    display_name: DisplayName | None = None


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH)


class SignInIn(BaseModel):
    username: str
    password: str


class AdminOut(BaseModel):
    username: str
    display_name: str
    created_at: datetime

    @classmethod
    def of(cls, admin: Admin) -> "AdminOut":
        return cls(
            username=admin.username, display_name=admin.display_name, created_at=admin.created_at
        )


class SetupIn(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH)
    display_name: str | None = Field(default=None, min_length=1, max_length=64)


@router.get("/setup/status")
def setup_status(conn: Db) -> dict[str, bool]:
    return {"admin_created": admin_store.get_admin(conn) is not None}


@router.post("/setup/admin", status_code=201)
def create_admin(body: SetupIn, conn: Db, request: Request, response: Response) -> AdminOut:
    try:
        admin = admin_store.create_admin(
            conn,
            username=body.username,
            display_name=body.display_name or body.username,
            password=body.password,
            now=datetime.now(UTC),
        )
    except admin_store.AdminExists:
        raise HTTPException(409, "The admin account already exists.") from None
    _set_session_cookie(response, request, sessions.create_session(conn, _now()))
    return AdminOut.of(admin)


@router.post("/auth/sign-in", status_code=204)
def sign_in(body: SignInIn, conn: Db, request: Request) -> Response:
    now = _now()
    until = sessions.locked_until(conn, now)
    if until is not None:
        # Commit nothing: a paused attempt is not a new failure.
        seconds = max(1, int((until - now).total_seconds()))
        return JSONResponse(
            {
                "detail": f"Sign-in is paused after {sessions.MAX_FAILURES} failed tries.",
                "locked_until": until.isoformat(),
            },
            status_code=429,
            headers={"Retry-After": str(seconds)},
        )
    admin = admin_store.get_admin(conn)
    if admin is not None and admin.username == body.username:
        ok = verify_password(body.password, admin.password_hash)
    else:
        verify_password(body.password, _DUMMY_HASH)
        ok = False
    if not ok:
        sessions.record_failure(conn, now)
        conn.commit()
        raise HTTPException(401, "That username or password doesn't match.")
    sessions.clear_failures(conn)
    response = Response(status_code=204)
    _set_session_cookie(response, request, sessions.create_session(conn, now))
    return response


@router.post("/auth/sign-out", status_code=204)
def sign_out(conn: Db, token: Annotated[str | None, Depends(session_token)]) -> Response:
    if token:
        sessions.delete_session(conn, token)
    response = Response(status_code=204)
    response.delete_cookie(sessions.SESSION_COOKIE, path="/")
    return response


@router.get("/me")
def me(admin: Annotated[Admin, Depends(require_admin)]) -> AdminOut:
    return AdminOut.of(admin)


@router.patch("/me")
def update_me(body: ProfileIn, conn: Db, _: Annotated[Admin, Depends(require_admin)]) -> AdminOut:
    admin = admin_store.update_admin(conn, username=body.username, display_name=body.display_name)
    return AdminOut.of(admin)


@router.post("/me/password", status_code=204)
def change_password(
    body: PasswordIn,
    conn: Db,
    admin: Annotated[Admin, Depends(require_admin)],
    token: Annotated[str | None, Depends(session_token)],
) -> Response:
    if not verify_password(body.current_password, admin.password_hash):
        raise HTTPException(400, "Your current password doesn't match.")
    admin_store.set_password(conn, body.new_password)
    sessions.delete_all_sessions(conn, except_token=token)
    return Response(status_code=204)

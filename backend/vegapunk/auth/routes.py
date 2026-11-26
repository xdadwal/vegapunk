import sqlite3
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from vegapunk.auth import admin as admin_store
from vegapunk.auth.admin import MIN_PASSWORD_LENGTH, USERNAME_PATTERN, Admin
from vegapunk.db import get_db

router = APIRouter(prefix="/api")

Db = Annotated[sqlite3.Connection, Depends(get_db)]


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
def create_admin(body: SetupIn, conn: Db) -> AdminOut:
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
    return AdminOut.of(admin)

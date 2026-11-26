import asyncio
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from logpose import AuthError, ProviderError
from pydantic import BaseModel

from vegapunk.ai import store
from vegapunk.ai.redact import scrub_secret
from vegapunk.api.queries import z
from vegapunk.auth.admin import Admin
from vegapunk.auth.routes import Db, require_admin
from vegapunk.db import connect

router = APIRouter(prefix="/api/settings")
Admin_ = Annotated[Admin, Depends(require_admin)]

DEFAULT_BASE_URL = "https://api.openai.com/v1"
TEST_TIMEOUT = 15  # seconds
MAX_MODELS = 200


class AiIn(BaseModel):
    base_url: str
    model: str
    instructions: str = ""
    api_key: str | None = None
    remove_key: bool = False


class TestIn(BaseModel):
    base_url: str
    api_key: str | None = None


def _payload(loaded: store.LoadedAi | None) -> dict:
    """The settings as the API shows them. The key itself is never part of this."""
    if loaded is None:
        return {
            "configured": False,
            "base_url": DEFAULT_BASE_URL,
            "model": "",
            "instructions": "",
            "key_saved": False,
            "key_hint": None,
            "key_rejected_at": None,
            "key_unreadable": False,
        }
    return {
        "configured": True,
        "base_url": loaded.config.base_url,
        "model": loaded.config.model,
        "instructions": loaded.config.instructions,
        "key_saved": loaded.key_saved,
        "key_hint": loaded.key_hint,
        "key_rejected_at": z(loaded.key_rejected_at),
        "key_unreadable": loaded.key_unreadable,
    }


@router.get("/ai")
def get_ai(conn: Db, request: Request, _: Admin_) -> dict:
    return _payload(store.load_ai(conn, request.app.state.crypto_key))


@router.put("/ai")
def put_ai(body: AiIn, conn: Db, request: Request, _: Admin_) -> dict:
    if body.remove_key and body.api_key is not None:
        raise HTTPException(422, "Send a new API key or remove the saved one, not both.")
    key = None if body.remove_key else (store.KEEP if body.api_key is None else body.api_key)
    crypto_key = request.app.state.crypto_key
    try:
        store.save_ai(
            conn,
            crypto_key,
            base_url=body.base_url,
            model=body.model,
            instructions=body.instructions,
            api_key=key,
            now=datetime.now(UTC),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return _payload(store.load_ai(conn, crypto_key))


@router.delete("/ai", status_code=204)
def delete_ai(conn: Db, _: Admin_) -> Response:
    store.clear_ai(conn)
    return Response(status_code=204)


def _load_saved(request: Request) -> store.LoadedAi | None:
    conn = connect(request.app.state.settings.db_path)
    try:
        return store.load_ai(conn, request.app.state.crypto_key)
    finally:
        conn.close()


def _clear_rejected(request: Request) -> None:
    conn = connect(request.app.state.settings.db_path)
    try:
        store.clear_key_rejected(conn)
    finally:
        conn.close()


@router.post("/ai/test")
async def test_ai(body: TestIn, request: Request, _: Admin_) -> dict:
    """Ask the server for its models. Always answers 200; `ok` says how it went."""
    try:
        url = store.normalise_base_url(body.base_url)
    except ValueError as exc:
        return {"ok": False, "code": "error", "message": str(exc)}

    saved = await run_in_threadpool(_load_saved, request)
    typed = body.api_key.strip() if body.api_key and body.api_key.strip() else None
    # The saved key is only ever used with the base URL it was saved for.
    same_server = saved is not None and saved.config.base_url == url
    key = typed
    if key is None and same_server:
        key = saved.config.api_key  # type: ignore[union-attr]
    tests_saved = same_server and (typed is None or typed == saved.config.api_key)  # type: ignore[union-attr]

    provider = request.app.state.make_provider(base_url=url, api_key=key or "")
    try:
        models = await asyncio.wait_for(provider.list_models(), TEST_TIMEOUT)
    except AuthError:
        return {"ok": False, "code": "key_rejected", "message": "The server rejected the API key."}
    except TimeoutError:
        return _unreachable(url)
    except ProviderError as exc:
        if exc.status_code is None:
            return _unreachable(url)
        hint = "Check the Base URL; it usually ends in /v1. " if exc.status_code == 404 else ""
        return {"ok": False, "code": "error", "message": hint + scrub_secret(exc.message, key)}
    except ValueError:  # a 200 that isn't JSON, such as a web page
        return {
            "ok": False,
            "code": "error",
            "message": (
                "That server didn't answer like an OpenAI-compatible API. Check the Base URL."
            ),
        }
    except Exception as exc:
        return {"ok": False, "code": "error", "message": scrub_secret(str(exc), key)}
    finally:
        await provider.aclose()

    if tests_saved:
        await run_in_threadpool(_clear_rejected, request)
    return {"ok": True, "models": sorted(set(models))[:MAX_MODELS]}


def _unreachable(url: str) -> dict:
    return {"ok": False, "code": "unreachable", "message": f"Couldn't reach {url}."}

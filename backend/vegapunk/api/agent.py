import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StrictInt, StringConstraints
from starlette.background import BackgroundTask

from vegapunk.ai import store
from vegapunk.ai.operator import OperatorBusy, OperatorOff, UnknownConversation, UnknownFinding
from vegapunk.auth.admin import Admin
from vegapunk.auth.routes import Db, require_admin
from vegapunk.db import connect

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent")
Admin_ = Annotated[Admin, Depends(require_admin)]

MAX_MESSAGE = 4_000
# How often an answer checks that the browser is still there while nothing is being sent.
DISCONNECT_POLL = 0.25
Message = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE)
]

FindingId = Annotated[StrictInt, Field(ge=1, le=2**63 - 1)]  # SQLite's integer range


class ChatIn(BaseModel):
    message: Message
    conversation_id: str | None = None
    # Ask about this finding. Ignored when `conversation_id` is given.
    finding_id: FindingId | None = None


def _line(event: dict) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode()


def _gone() -> JSONResponse:
    return JSONResponse({"detail": "That conversation no longer exists."}, status_code=404)


def _busy(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": "busy"}, status_code=409)


class _Answer:
    """One answer streamed as NDJSON lines, with its run in a task of its own.

    When the browser leaves (a refresh, or leaving the page) the run is cancelled exactly
    once, so the operator's cleanup (closing the agent, recording the end of the answer)
    runs to the end before the agent takes the next question. Starlette's own cancellation
    of the response would cancel every await of that cleanup too, and from ASGI spec 2.4
    Starlette stops watching for the client at all: a gone client is only noticed when a
    write fails, and nothing is written while a slow model is silent. So the answer
    watches for the client itself.
    """

    def __init__(self, events: AsyncIterator[dict], first: dict, request: Request) -> None:
        self._events, self._first, self._request = events, first, request
        self._lines: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._run: asyncio.Task | None = None
        self._stopping = False

    async def _produce(self) -> None:
        try:
            async with aclosing(self._events):
                async for event in self._events:
                    self._lines.put_nowait(_line(event))
        except Exception:
            log.exception("An answer stopped unexpectedly")
        finally:
            self._lines.put_nowait(None)

    async def _watch(self) -> None:
        try:
            while not await self._request.is_disconnected():
                await asyncio.sleep(DISCONNECT_POLL)
        except Exception:
            log.exception("Could not watch for the browser leaving")
            return
        self._stop()

    def _stop(self) -> None:
        # Once only: a second cancel would cut short the cleanup the first one started.
        if self._run is not None and not self._stopping:
            self._stopping = True
            self._run.cancel()

    async def _ended(self) -> None:
        if self._run is not None:
            await asyncio.wait({self._run})  # waits without passing a cancel on to the run

    async def stream(self) -> AsyncIterator[bytes]:
        self._run = asyncio.create_task(self._produce())
        watcher = asyncio.create_task(self._watch())
        try:
            yield _line(self._first)
            while (line := await self._lines.get()) is not None:
                yield line
        finally:
            watcher.cancel()
            self._stop()
            await self._ended()

    async def close(self) -> None:
        """After the response: end a run that was never started, or wait for its end."""
        if self._run is None:
            await self._events.aclose()
        else:
            self._stop()
            await self._ended()


@router.post("/chat")
async def chat(body: ChatIn, request: Request, _: Admin_):
    events = request.app.state.operator.chat(body.message, body.conversation_id, body.finding_id)
    # Every check that can fail happens before the first event, so pulling one event first
    # turns those failures into proper HTTP errors before any response has started.
    try:
        first = await events.__anext__()
    except OperatorOff:
        return JSONResponse(
            {"detail": "Connect an AI provider first.", "code": "ai_off"}, status_code=409
        )
    except OperatorBusy:
        return _busy("The agent is already answering.")
    except UnknownConversation:
        return _gone()
    except UnknownFinding:
        return JSONResponse({"detail": "That finding no longer exists."}, status_code=404)

    answer = _Answer(events, first, request)
    return StreamingResponse(
        answer.stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        # Covers a body that is never iterated (the client left before streaming began).
        background=BackgroundTask(answer.close),
    )


@router.get("/conversations")
def conversations(conn: Db, _: Admin_) -> dict:
    return {"items": store.list_conversations(conn)}


@router.get("/conversations/{conversation_id}")
async def conversation(conversation_id: str, request: Request, _: Admin_):
    operator = request.app.state.operator
    try:
        messages = await operator.history(conversation_id)
    except UnknownConversation:
        return _gone()

    def load():
        conn = connect(request.app.state.settings.db_path)
        try:
            return store.get_conversation(conn, conversation_id)
        finally:
            conn.close()

    found = await run_in_threadpool(load)
    if found is None:  # deleted while we were reading it
        return _gone()
    return {**found, "available": bool(messages), "messages": messages}


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request, _: Admin_):
    try:
        await request.app.state.operator.delete(conversation_id)
    except UnknownConversation:
        return _gone()
    except OperatorBusy:
        return _busy("The agent is answering. Try again when it has finished.")
    return Response(status_code=204)

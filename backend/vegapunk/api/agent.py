import json
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, StringConstraints
from starlette.background import BackgroundTask

from vegapunk.ai import store
from vegapunk.ai.operator import OperatorBusy, OperatorOff, UnknownConversation
from vegapunk.auth.admin import Admin
from vegapunk.auth.routes import Db, require_admin
from vegapunk.db import connect

router = APIRouter(prefix="/api/agent")
Admin_ = Annotated[Admin, Depends(require_admin)]

MAX_MESSAGE = 4_000
Message = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE)
]


class ChatIn(BaseModel):
    message: Message
    conversation_id: str | None = None


def _line(event: dict) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode()


def _gone() -> JSONResponse:
    return JSONResponse({"detail": "That conversation no longer exists."}, status_code=404)


def _busy(detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail, "code": "busy"}, status_code=409)


@router.post("/chat")
async def chat(body: ChatIn, request: Request, _: Admin_):
    events = request.app.state.operator.chat(body.message, body.conversation_id)
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

    async def stream() -> AsyncIterator[bytes]:
        # aclosing ends the run and frees the session when the client leaves mid-answer.
        async with aclosing(events):
            yield _line(first)
            async for event in events:
                yield _line(event)

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        # Covers a body that is never iterated (the client left before streaming began).
        background=BackgroundTask(events.aclose),
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

import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, StrictInt, StringConstraints

from vegapunk.ai import store
from vegapunk.ai.operator import (
    Answer,
    OperatorBusy,
    OperatorOff,
    UnknownConversation,
    UnknownFinding,
)
from vegapunk.api.security import AppHeader
from vegapunk.auth.admin import Admin
from vegapunk.auth.routes import Db, require_admin
from vegapunk.db import connect

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/agent")
Admin_ = Annotated[Admin, Depends(require_admin)]

MAX_MESSAGE = 4_000
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


def _idle() -> JSONResponse:
    return JSONResponse({"detail": "No answer is running.", "code": "idle"}, status_code=404)


def _stream(answer: Answer) -> StreamingResponse:
    """The answer as NDJSON lines: what was said so far, then live until it ends.

    The client leaving ends only this response: the answer carries on. The reader is an
    async generator that registers itself on its first step and unregisters in its
    `finally`, so a body that is never iterated, or is cancelled, leaves nothing behind.
    """

    async def lines() -> AsyncIterator[bytes]:
        async with aclosing(answer.subscribe()) as events:
            async for event in events:
                yield _line(event)

    return StreamingResponse(
        lines(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/chat")
async def chat(body: ChatIn, request: Request, _: Admin_):
    operator = request.app.state.operator
    # Every check that can fail happens before the answer's first event, so those failures
    # are proper HTTP errors before any response has started.
    try:
        answer = await operator.start_answer(body.message, body.conversation_id, body.finding_id)
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
    return _stream(answer)


async def _not_running(request: Request, conversation_id: str) -> JSONResponse:
    """404 "idle" for a conversation with nothing running, the usual 404 for no such one."""
    try:
        await request.app.state.operator.conversation(conversation_id)
    except UnknownConversation:
        return _gone()
    return _idle()


@router.get("/conversations/{conversation_id}/stream")
async def follow(conversation_id: str, request: Request, _: Admin_):
    answer = request.app.state.operator.active(conversation_id)
    if answer is None:
        return await _not_running(request, conversation_id)
    return _stream(answer)


@router.post("/conversations/{conversation_id}/stop", status_code=204)
async def stop(conversation_id: str, request: Request, _: Admin_, __: None = AppHeader):
    if not await request.app.state.operator.stop(conversation_id):
        return await _not_running(request, conversation_id)
    return Response(status_code=204)


@router.get("/conversations")
def conversations(conn: Db, request: Request, _: Admin_) -> dict:
    operator = request.app.state.operator
    items = store.list_conversations(conn)
    return {"items": [{**i, "answering": operator.active(i["id"]) is not None} for i in items]}


@router.get("/conversations/{conversation_id}")
async def conversation(conversation_id: str, request: Request, _: Admin_):
    operator = request.app.state.operator
    answer = operator.active(conversation_id)
    try:
        messages = await operator.history(conversation_id)
        if answer is not None and answer.done:  # it ended meanwhile: now it is stored
            answer, messages = None, await operator.history(conversation_id)
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
    if answer is not None:
        # The answer being written is stored, with its question, only when it ends.
        messages = [*messages, {"role": "user", "text": answer.question, "steps": []}]
    return {
        **found,
        "answering": answer is not None,
        "available": bool(messages),
        "messages": messages,
    }


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: str, request: Request, _: Admin_):
    try:
        await request.app.state.operator.delete(conversation_id)
    except UnknownConversation:
        return _gone()
    except OperatorBusy:
        return _busy("The agent is answering. Try again when it has finished.")
    return Response(status_code=204)

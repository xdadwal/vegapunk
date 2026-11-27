"""A scriptable imitation of an OpenAI-compatible server, for end-to-end tests.

It speaks the real wire format (server-sent events with `data: {...}` lines and a final
`[DONE]`), so logpose talks to it exactly as it would to OpenAI, Ollama or vLLM. It records
every request it receives so tests can check what was sent.
"""

import asyncio
import json
import socket
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse


@dataclass
class FakeOpenAI:
    """`script` has one entry per chat request, each one of:

    - {"text": ["chunk", ...], "chunk_delay": seconds}
    - {"tool_calls": [{"id": "call_1", "name": "list_containers", "arguments": {...}}]}
    - {"status": 500}            an HTTP error
    - {"status": 400, "message": "..."}  an HTTP error with that error message
    - {"hang": True}             never answers
    - {"wait": threading.Event(), ...}  sends nothing until the test sets the event, then
      plays the rest of the entry (a slow model that is silent while it thinks)
    - {"text": [...], "pause": threading.Event(), "pause_at": 2}  sends the chunks before
      index `pause_at`, then nothing until the test sets the event
    """

    script: list[dict] = field(default_factory=list)
    models: list[str] = field(default_factory=lambda: ["model-b", "model-a"])
    required_key: str | None = None
    requests: list[dict] = field(default_factory=list)  # chat requests
    model_requests: list[dict] = field(default_factory=list)  # model-list requests

    def tool_message_texts(self, request_index: int) -> list[str]:
        body = self.requests[request_index]["body"]
        return [m["content"] for m in body["messages"] if m["role"] == "tool"]


def _sse(obj: dict) -> str:
    return f"data: {json.dumps(obj)}\n\n"


def create_app(fake: FakeOpenAI) -> FastAPI:
    app = FastAPI()

    def authorised(request: Request) -> bool:
        if fake.required_key is None:
            return True
        return request.headers.get("authorization") == f"Bearer {fake.required_key}"

    def rejected() -> JSONResponse:
        return JSONResponse({"error": {"message": "Incorrect API key provided."}}, status_code=401)

    @app.get("/v1/models")
    async def models(request: Request):
        fake.model_requests.append({"authorization": request.headers.get("authorization")})
        if not authorised(request):
            return rejected()
        return {"object": "list", "data": [{"id": m, "object": "model"} for m in fake.models]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        if not authorised(request):
            return rejected()
        body = await request.json()
        index = len(fake.requests)
        fake.requests.append({"authorization": request.headers.get("authorization"), "body": body})
        if index >= len(fake.script):
            return JSONResponse({"error": {"message": "script exhausted"}}, status_code=500)
        turn = fake.script[index]
        if turn.get("hang"):
            await asyncio.sleep(3600)
        if "wait" in turn:
            while not turn["wait"].is_set():
                await asyncio.sleep(0.01)
        if "status" in turn:
            return JSONResponse(
                {"error": {"message": turn.get("message", "scripted failure")}},
                status_code=turn["status"],
            )

        base = {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": body["model"],
        }

        def chunk(delta, finish=None):
            return _sse(
                {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
            )

        async def stream():
            yield chunk({"role": "assistant", "content": ""})
            if "tool_calls" in turn:
                calls = [
                    {
                        "index": i,
                        "id": c.get("id", f"call_{i}"),
                        "type": "function",
                        "function": {
                            "name": c["name"],
                            "arguments": json.dumps(c.get("arguments", {})),
                        },
                    }
                    for i, c in enumerate(turn["tool_calls"])
                ]
                yield chunk({"tool_calls": calls})
                yield chunk({}, "tool_calls")
            else:
                for index, piece in enumerate(turn["text"]):
                    if "pause" in turn and index == turn.get("pause_at", 0):
                        while not turn["pause"].is_set():
                            await asyncio.sleep(0.01)
                    yield chunk({"content": piece})
                    if turn.get("chunk_delay"):
                        await asyncio.sleep(turn["chunk_delay"])
                yield chunk({}, "stop")
            usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
            yield _sse({**base, "choices": [], "usage": usage})
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@contextmanager
def serve(app, graceful: float = 1) -> Iterator[str]:
    """Run an ASGI app on a free local port in a thread. Yields its base URL.

    `graceful` is how long shutdown waits for open requests before cancelling them."""
    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            timeout_graceful_shutdown=graceful,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started:
        assert time.monotonic() < deadline, "server did not start"
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=10)

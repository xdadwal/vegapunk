"""Leaving the page mid-answer, against a real server and a real socket.

The test client never disconnects. Uvicorn reports ASGI spec 2.3 for HTTP, so Starlette
listens for the client leaving and cancels the response; from spec 2.4 it stops listening
and only a failed write would show the client is gone, which never happens while a slow
model is silent. Each test runs both ways: the run must end soon after the client leaves,
its cleanup must finish, and the next question (after a refresh) must not get 409.
"""

import json
import threading
import time
from contextlib import contextmanager

import httpx
import pytest

from tests.agent_helpers import FakeDocker
from tests.conftest import CREDENTIALS
from tests.fake_openai import FakeOpenAI, create_app, serve
from tests.helpers import listing, now, obs
from vegapunk.app import create_app as create_vegapunk
from vegapunk.db import connect

GONE_WITHIN = 2.0  # seconds


@pytest.fixture(params=["2.3", "2.4"])
def spec(request) -> str:
    return request.param


def with_spec(app, version: str):
    """`app` as a server speaking this ASGI spec version would call it."""

    async def wrapped(scope, receive, send):
        if scope["type"] == "http":
            scope = {**scope, "asgi": {**scope.get("asgi", {}), "spec_version": version}}
        await app(scope, receive, send)

    return wrapped


@contextmanager
def stack(settings, fake, spec, docker=None):
    """Vegapunk and the fake model running, signed in, with the provider saved.

    Yields (client, app, url)."""
    with serve(create_app(fake)) as model_url:
        app = create_vegapunk(settings, docker=docker or FakeDocker())
        with serve(with_spec(app, spec)) as url, httpx.Client(base_url=url, timeout=30) as client:
            assert client.post("/api/setup/admin", json=CREDENTIALS).status_code == 201
            saved = client.put(
                "/api/settings/ai",
                json={
                    "base_url": f"{model_url}/v1",
                    "model": "test-model",
                    "instructions": "",
                    "api_key": "sk-disconnect-0001",
                },
            )
            assert saved.status_code == 200, saved.text
            conn = connect(settings.db_path)
            listing(conn, now(), obs("a", "web"))
            conn.close()
            yield client, app, url


def wait_for(condition, timeout=5.0, what="condition"):
    deadline = time.monotonic() + timeout
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.01)


def ask(client, message="Second question?", conversation_id=None):
    with client.stream(
        "POST", "/api/agent/chat", json={"message": message, "conversation_id": conversation_id}
    ) as response:
        assert response.status_code == 200, response.read()
        return [json.loads(line) for line in response.iter_lines() if line.strip()]


def leave(client, url, before_closing=lambda events: True, message="Is anything wrong?"):
    """Start an answer on its own connection, read until `before_closing(events)` is true,
    then drop the connection, as a refresh does. Returns the events read."""
    events: list[dict] = []
    with httpx.Client(base_url=url, cookies=client.cookies, timeout=30) as own:
        with own.stream("POST", "/api/agent/chat", json={"message": message}) as response:
            assert response.status_code == 200, response.read()
            for line in response.iter_lines():
                if line.strip():
                    events.append(json.loads(line))
                if before_closing(events):
                    break
    return events


def in_silence(fake):
    """Close once the first event is in and the model has been asked (and says nothing)."""

    def ready(events):
        if not events:
            return False
        wait_for(lambda: len(fake.requests) == 1, what="the model to be asked")
        return True

    return ready


def touched(settings, cid) -> bool:
    """Whether the end of an answer was recorded on the conversation."""
    conn = connect(settings.db_path)
    try:
        created, updated = conn.execute(
            "SELECT created_at, updated_at FROM conversations WHERE id = ?", (cid,)
        ).fetchone()
    finally:
        conn.close()
    return updated > created


def assert_freed(app, client):
    """The run ended soon after the client left, its cleanup finished, and the agent takes
    a new question."""
    operator = app.state.operator
    wait_for(lambda: not operator._lock.locked(), GONE_WITHIN, "the agent to be freed")
    events = ask(client)
    assert events[0]["type"] == "conversation" and events[-1]["type"] == "done", events
    return events


def test_leaving_while_the_model_is_silent_frees_the_agent(settings, spec):
    gate = threading.Event()
    fake = FakeOpenAI(script=[{"wait": gate, "text": ["Too late."]}, {"text": ["Second."]}])
    try:
        with stack(settings, fake, spec) as (client, app, url):
            first = leave(client, url, in_silence(fake))
            assert first[0]["type"] == "conversation"
            started = time.monotonic()
            events = assert_freed(app, client)
            assert time.monotonic() - started < GONE_WITHIN + 1
            assert "".join(e["text"] for e in events if e["type"] == "text") == "Second."
            assert touched(settings, first[0]["id"])
            gate.set()
    finally:
        gate.set()


def test_leaving_right_after_the_first_event_frees_the_agent(settings, spec):
    gate = threading.Event()
    fake = FakeOpenAI(script=[{"wait": gate, "text": ["Too late."]}, {"text": ["Second."]}])
    try:
        with stack(settings, fake, spec) as (client, app, url):
            first = leave(client, url, lambda events: bool(events))
            assert [e["type"] for e in first] == ["conversation"]
            operator = app.state.operator
            wait_for(lambda: not operator._lock.locked(), GONE_WITHIN, "the agent to be freed")
            # The run may have ended before it reached the model, leaving the silent turn
            # for the next question: let it answer.
            gate.set()
            assert_freed(app, client)
    finally:
        gate.set()


class SlowDocker(FakeDocker):
    """Log reads that block until the test lets them go."""

    def __init__(self, logs):
        super().__init__(logs)
        self.started = threading.Event()
        self.release = threading.Event()

    def container_logs(self, container_id, tail):
        self.started.set()
        self.release.wait(timeout=30)
        return super().container_logs(container_id, tail)


def test_leaving_while_a_tool_runs_frees_the_agent(settings, spec):
    docker = SlowDocker({"a": ["boot ok"]})
    fake = FakeOpenAI(
        script=[
            {"tool_calls": [{"id": "L1", "name": "container_logs", "arguments": {"name": "web"}}]},
            {"text": ["Too late."]},
            {"text": ["Second."]},
        ]
    )
    try:
        with stack(settings, fake, spec, docker=docker) as (client, app, url):
            first = leave(
                client,
                url,
                lambda events: (
                    bool(events)
                    and events[-1].get("type") == "step"
                    and docker.started.wait(timeout=5)
                ),
            )
            assert first[-1]["status"] == "running"
            assert_freed(app, client)
            assert touched(settings, first[0]["id"])
            # The abandoned run never reached the model again.
            assert len(fake.requests) == 2
            docker.release.set()
    finally:
        docker.release.set()


def test_a_full_answer_still_arrives_in_order(settings, spec):
    fake = FakeOpenAI(
        script=[
            {"tool_calls": [{"id": "c1", "name": "list_containers", "arguments": {}}]},
            {"text": ["All ", "is ", "well."], "chunk_delay": 0.3},
        ]
    )
    with stack(settings, fake, spec) as (client, app, _):
        events = ask(client, "Is anything wrong?")
        assert [e["type"] for e in events] == [
            "conversation",
            "step",
            "step",
            "text",
            "text",
            "text",
            "done",
        ]
        assert "".join(e["text"] for e in events if e["type"] == "text") == "All is well."
        assert not app.state.operator._lock.locked()


def test_an_interrupted_answer_leaves_a_conversation_that_still_loads(settings, spec):
    gate = threading.Event()
    fake = FakeOpenAI(script=[{"wait": gate, "text": ["Too late."]}, {"text": ["Second."]}])
    try:
        with stack(settings, fake, spec) as (client, app, url):
            first = leave(client, url, in_silence(fake))
            cid = first[0]["id"]
            operator = app.state.operator
            wait_for(lambda: not operator._lock.locked(), GONE_WITHIN, "the agent to be freed")

            assert touched(settings, cid)  # the end of the answer was recorded

            loaded = client.get(f"/api/agent/conversations/{cid}")
            assert loaded.status_code == 200, loaded.text
            messages = loaded.json()["messages"]
            # The question is kept; no half-written answer is.
            assert [m["role"] for m in messages] == ["user"]
            assert messages[0]["text"] == "Is anything wrong?"

            # The conversation carries on.
            again = ask(client, "Still there?", conversation_id=cid)
            assert again[-1]["type"] == "done"
            shown = client.get(f"/api/agent/conversations/{cid}").json()["messages"]
            assert [m["role"] for m in shown][-2:] == ["user", "assistant"]
            gate.set()
    finally:
        gate.set()

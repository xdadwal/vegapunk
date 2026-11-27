"""Leaving the page mid-answer, against a real server and a real socket.

The answer runs on the server in a task of its own: the browser leaving (a refresh, or
going to another page) ends only that response. The answer carries on and is stored, the
conversation says it is answering meanwhile, a reader can reattach and get what was said
so far before following it live, and an explicit Stop cancels it.

Uvicorn reports ASGI spec 2.3 for HTTP, so Starlette listens for the client leaving and
cancels the response; from spec 2.4 it stops listening and only a failed write shows the
client is gone. Each test runs both ways.
"""

import gc
import json
import logging
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

KEY = "sk-disconnect-0001"
APP = {"X-Requested-With": "vegapunk"}
STOPPED = {"type": "error", "code": "stopped", "message": "Stopped."}
IDLE = {"detail": "No answer is running.", "code": "idle"}
GONE = {"detail": "That conversation no longer exists."}
WITHIN = 2.0  # seconds


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
                    "api_key": KEY,
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


def lines_of(response) -> list[dict]:
    return [json.loads(line) for line in response.iter_lines() if line.strip()]


def ask(client, message="Second question?", conversation_id=None):
    with client.stream(
        "POST", "/api/agent/chat", json={"message": message, "conversation_id": conversation_id}
    ) as response:
        assert response.status_code == 200, response.read()
        return lines_of(response)


def read_until(client, method, path, until, **kwargs):
    """Open a stream on its own connection, read until `until(events)` is true, then drop
    the connection, as a refresh does. Returns the events read."""
    events: list[dict] = []
    with httpx.Client(base_url=str(client.base_url), cookies=client.cookies, timeout=30) as own:
        with own.stream(method, path, **kwargs) as response:
            assert response.status_code == 200, response.read()
            for line in response.iter_lines():
                if line.strip():
                    events.append(json.loads(line))
                    assert KEY not in line
                if until(events):
                    break
    return events


def leave(client, until, message="Is anything wrong?"):
    return read_until(client, "POST", "/api/agent/chat", until, json={"message": message})


def text_of(events) -> str:
    return "".join(e["text"] for e in events if e["type"] == "text")


def asked(fake, n=1):
    """Close once the first event is in and the model has been asked `n` times."""

    def ready(events):
        if not events:
            return False
        wait_for(lambda: len(fake.requests) == n, what="the model to be asked")
        return True

    return ready


def detail(client, cid) -> dict:
    r = client.get(f"/api/agent/conversations/{cid}")
    assert r.status_code == 200, r.text
    assert KEY not in r.text
    return r.json()


def answering(client, cid) -> bool:
    return detail(client, cid)["answering"]


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


def test_leaving_mid_answer_keeps_it_running_and_it_is_stored(settings, spec):
    gate = threading.Event()
    fake = FakeOpenAI(script=[{"wait": gate, "text": ["Finished ", "anyway."]}, {"text": ["2"]}])
    try:
        with stack(settings, fake, spec) as (client, app, _):
            first = leave(client, asked(fake))
            cid = first[0]["id"]
            time.sleep(0.3)  # long enough for a cancellation to have shown, had there been one
            shown = detail(client, cid)
            assert shown["answering"] is True and shown["available"] is True
            # Stored only when it ends: the question being answered is shown meanwhile.
            assert shown["messages"] == [
                {"role": "user", "text": "Is anything wrong?", "steps": []}
            ]
            (item,) = client.get("/api/agent/conversations").json()["items"]
            assert item["answering"] is True

            busy = client.post("/api/agent/chat", json={"message": "Another?"})
            assert busy.status_code == 409
            assert busy.json() == {"detail": "The agent is already answering.", "code": "busy"}

            gate.set()
            wait_for(lambda: not answering(client, cid), what="the answer to finish")
            shown = detail(client, cid)
            assert shown["available"] is True
            assert [(m["role"], m["text"]) for m in shown["messages"]] == [
                ("user", "Is anything wrong?"),
                ("assistant", "Finished anyway."),
            ]
            assert touched(settings, cid)
            assert not app.state.operator._lock.locked()
            assert ask(client, "Next?", cid)[-1]["type"] == "done"
    finally:
        gate.set()


def test_reattaching_replays_what_was_said_then_follows_to_the_end(settings, spec):
    pause = threading.Event()
    fake = FakeOpenAI(script=[{"text": ["one ", "two ", "three"], "pause": pause, "pause_at": 2}])
    try:
        with stack(settings, fake, spec) as (client, _, _):
            first = leave(client, lambda events: text_of(events) == "one two ")
            cid = first[0]["id"]
            path = f"/api/agent/conversations/{cid}/stream"
            # A refresh: the replay so far, coalesced, and the live end.
            replay = read_until(client, "GET", path, lambda e: text_of(e) == "one two ")
            assert replay == [first[0], {"type": "text", "text": "one two "}]

            with client.stream("GET", path) as response:
                assert response.status_code == 200
                assert response.headers["content-type"].startswith("application/x-ndjson")
                assert response.headers["cache-control"] == "no-store"
                lines = response.iter_lines()
                head = [json.loads(next(lines)), json.loads(next(lines))]
                assert head == replay
                pause.set()
                rest = [json.loads(line) for line in lines if line.strip()]
            events = head + rest
            assert text_of(events) == "one two three"
            assert [e["type"] for e in events].count("conversation") == 1
            assert events[-1]["type"] == "done"
            wait_for(lambda: not answering(client, cid), what="the answer to finish")
            assert client.get(path).json() == IDLE
    finally:
        pause.set()


def test_reattaching_before_any_text_gets_the_whole_answer(settings, spec):
    gate = threading.Event()
    fake = FakeOpenAI(script=[{"wait": gate, "text": ["All ", "is ", "well."]}])
    try:
        with stack(settings, fake, spec) as (client, _, _):
            cid = leave(client, asked(fake))[0]["id"]
            with client.stream("GET", f"/api/agent/conversations/{cid}/stream") as response:
                lines = response.iter_lines()
                assert json.loads(next(lines))["type"] == "conversation"
                gate.set()
                rest = [json.loads(line) for line in lines if line.strip()]
            assert text_of(rest) == "All is well." and rest[-1]["type"] == "done"
    finally:
        gate.set()


def test_two_readers_at_once_both_get_the_whole_answer(settings, spec):
    gate = threading.Event()
    fake = FakeOpenAI(script=[{"wait": gate, "text": ["Both ", "of ", "you."]}])
    try:
        with stack(settings, fake, spec) as (client, _, url):
            cid = leave(client, asked(fake))[0]["id"]
            path = f"/api/agent/conversations/{cid}/stream"
            got: dict[int, list[dict]] = {}
            heads = threading.Barrier(3, timeout=10)

            def reader(n):
                with httpx.Client(base_url=url, cookies=client.cookies, timeout=30) as own:
                    with own.stream("GET", path) as response:
                        lines = response.iter_lines()
                        got[n] = [json.loads(next(lines))]
                        heads.wait()
                        got[n] += [json.loads(line) for line in lines if line.strip()]

            threads = [threading.Thread(target=reader, args=(n,)) for n in (1, 2)]
            for t in threads:
                t.start()
            heads.wait()
            gate.set()
            for t in threads:
                t.join(timeout=10)
            for n in (1, 2):
                assert text_of(got[n]) == "Both of you." and got[n][-1]["type"] == "done"
    finally:
        gate.set()


def test_stop_ends_the_answer_frees_the_agent_and_keeps_the_question(settings, spec):
    gate = threading.Event()
    fake = FakeOpenAI(script=[{"wait": gate, "text": ["Too late."]}, {"text": ["Second."]}])
    try:
        with stack(settings, fake, spec) as (client, app, _):
            cid = leave(client, asked(fake))[0]["id"]
            stop = f"/api/agent/conversations/{cid}/stop"
            assert client.post(stop).status_code == 403  # no app header
            assert answering(client, cid)

            follower: list[dict] = []
            with client.stream("GET", f"/api/agent/conversations/{cid}/stream") as response:
                lines = response.iter_lines()
                follower.append(json.loads(next(lines)))
                started = time.monotonic()
                r = client.post(stop, headers=APP)
                assert r.status_code == 204 and r.content == b""
                assert time.monotonic() - started < WITHIN
                follower += [json.loads(line) for line in lines if line.strip()]
            assert follower[-1] == STOPPED

            assert not app.state.operator._lock.locked()
            assert client.post(stop, headers=APP).json() == IDLE
            shown = detail(client, cid)
            assert shown["answering"] is False
            assert [m["role"] for m in shown["messages"]] == ["user"]
            assert touched(settings, cid)
            again = ask(client, "Still there?", cid)
            assert text_of(again) == "Second." and again[-1]["type"] == "done"
            assert [m["role"] for m in detail(client, cid)["messages"]][-2:] == [
                "user",
                "assistant",
            ]
    finally:
        gate.set()


def test_stream_and_stop_for_unknown_ids_are_404(settings, spec):
    with stack(settings, FakeOpenAI(), spec) as (client, _, _):
        for bad in ["z" * 32, "short"]:
            assert client.get(f"/api/agent/conversations/{bad}/stream").json() == GONE
            r = client.post(f"/api/agent/conversations/{bad}/stop", headers=APP)
            assert r.status_code == 404 and r.json() == GONE


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


def test_leaving_while_a_tool_runs_lets_the_answer_finish(settings, spec):
    docker = SlowDocker({"a": ["boot ok"]})
    fake = FakeOpenAI(
        script=[
            {"tool_calls": [{"id": "L1", "name": "container_logs", "arguments": {"name": "web"}}]},
            {"text": ["Its log looks fine."]},
        ]
    )
    try:
        with stack(settings, fake, spec, docker=docker) as (client, _, _):
            first = leave(
                client,
                lambda events: (
                    bool(events)
                    and events[-1].get("type") == "step"
                    and docker.started.wait(timeout=5)
                ),
            )
            assert first[-1]["status"] == "running"
            cid = first[0]["id"]
            assert answering(client, cid)
            docker.release.set()
            wait_for(lambda: not answering(client, cid), what="the answer to finish")
            assert len(fake.requests) == 2
            (_, answer) = detail(client, cid)["messages"]
            assert answer["text"] == "Its log looks fine."
            assert [s["status"] for s in answer["steps"]] == ["done"]
    finally:
        docker.release.set()


def test_stopping_while_a_tool_runs_stores_a_failed_step(settings, spec):
    docker = SlowDocker({"a": ["boot ok"]})
    fake = FakeOpenAI(
        script=[
            {"tool_calls": [{"id": "L1", "name": "container_logs", "arguments": {"name": "web"}}]},
            {"text": ["Never."]},
        ]
    )
    try:
        with stack(settings, fake, spec, docker=docker) as (client, app, _):
            first = leave(
                client,
                lambda events: bool(events) and events[-1].get("type") == "step",
            )
            cid = first[0]["id"]
            assert docker.started.wait(timeout=5)
            stop = client.post(f"/api/agent/conversations/{cid}/stop", headers=APP)
            assert stop.status_code == 204
            assert not app.state.operator._lock.locked()
            (_, answer) = detail(client, cid)["messages"]
            assert [s["status"] for s in answer["steps"]] == ["failed"]
            docker.release.set()
            assert len(fake.requests) == 1  # the stopped answer never asked the model again
    finally:
        docker.release.set()


def test_shutting_down_mid_answer_stops_it_cleanly(settings, spec, caplog):
    gate = threading.Event()
    fake = FakeOpenAI(script=[{"wait": gate, "text": ["Never."]}])
    try:
        with caplog.at_level(logging.WARNING):
            with stack(settings, fake, spec) as (client, app, _):
                cid = leave(client, asked(fake))[0]["id"]
                operator = app.state.operator
                assert operator.active(cid) is not None
            # The server and its lifespan have ended here.
            gc.collect()
        assert operator.active(cid) is None and not operator._running
        assert not operator._lock.locked()
        assert touched(settings, cid)
        assert "destroyed" not in caplog.text and "never retrieved" not in caplog.text
    finally:
        gate.set()


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
        assert text_of(events) == "All is well."
        assert not app.state.operator._lock.locked()
        assert detail(client, events[0]["id"])["answering"] is False

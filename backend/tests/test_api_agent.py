import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from tests.agent_helpers import FakeProvider, Turn, agent_factory
from tests.conftest import CREDENTIALS
from tests.helpers import listing, now, obs
from vegapunk.ai import operator as operator_module
from vegapunk.app import create_app
from vegapunk.db import connect

SETTINGS_BODY = {
    "base_url": "https://api.example.com/v1",
    "model": "test-model",
    "instructions": "",
    "api_key": "sk-test-key-abcdef123456",
}


def build(settings, turns, *, configured=True, repeat_last=False):
    provider = FakeProvider(turns, repeat_last=repeat_last)
    app = create_app(settings, make_agent=agent_factory(provider))
    client = TestClient(app)
    client.__enter__()
    assert client.post("/api/setup/admin", json=CREDENTIALS).status_code == 201
    if configured:
        assert client.put("/api/settings/ai", json=SETTINGS_BODY).status_code == 200
    conn = connect(settings.db_path)
    listing(conn, now(), obs("a", "web"))
    conn.close()
    return client, provider


@pytest.fixture
def agent(settings):
    client, provider = build(settings, [Turn.text("All good.", chunks=["All ", "good."])])
    yield client, provider
    client.__exit__(None, None, None)


def events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


def chat(client, message="Is anything wrong?", conversation_id=None):
    return client.post(
        "/api/agent/chat", json={"message": message, "conversation_id": conversation_id}
    )


def test_agent_routes_require_sign_in(client):
    assert client.post("/api/agent/chat", json={"message": "hi"}).status_code == 401
    assert client.get("/api/agent/conversations").status_code == 401
    assert client.get("/api/agent/conversations/" + "a" * 32).status_code == 401
    assert client.delete("/api/agent/conversations/" + "a" * 32).status_code == 401


def test_chat_streams_json_lines_with_the_right_headers(agent):
    client, _ = agent
    r = chat(client)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-accel-buffering"] == "no"
    got = events(r)
    assert [e["type"] for e in got] == ["conversation", "text", "text", "done"]
    assert r.text.endswith("\n") and all(line.startswith("{") for line in r.text.splitlines())


@pytest.mark.parametrize("message", ["", "   ", "\n\t", "x" * 4_001])
def test_chat_rejects_empty_whitespace_and_over_long_messages(agent, message):
    client, provider = agent
    assert chat(client, message).status_code == 422
    assert client.get("/api/agent/conversations").json() == {"items": []}
    assert provider.requests == []


def test_a_message_at_the_limit_is_accepted_and_is_trimmed(settings):
    client, provider = build(settings, [Turn.text("ok")])
    with client:
        assert chat(client, "  " + "x" * 4_000 + "  ").status_code == 200
    assert len(provider.requests[0].messages[0].content[0].text) == 4_000


def test_chat_without_ai_settings_is_409_ai_off(settings):
    client, _ = build(settings, [Turn.text("x")], configured=False)
    with client:
        r = chat(client)
        assert r.status_code == 409
        assert r.json() == {"detail": "Connect an AI provider first.", "code": "ai_off"}
        assert client.get("/api/agent/conversations").json() == {"items": []}


def test_chat_with_an_unknown_conversation_is_404(agent):
    client, _ = agent
    for bad in ["z" * 32, "../x", "a" * 200, ""]:
        r = chat(client, conversation_id=bad)
        assert r.status_code == 404 and r.json() == {
            "detail": "That conversation no longer exists."
        }


def test_chat_while_answering_is_409_busy(settings, monkeypatch):
    monkeypatch.setattr(operator_module, "RUN_TIMEOUT", 1.5)
    client, _ = build(settings, [Turn.hang(), Turn.text("later")])
    with client:
        holder = {}
        thread = threading.Thread(target=lambda: holder.update(r=chat(client, "first")))
        thread.start()
        deadline = time.monotonic() + 5
        while not client.app.state.operator._lock.locked():
            assert time.monotonic() < deadline, "the first answer never started"
            time.sleep(0.02)
        busy = chat(client, "second")
        thread.join(timeout=10)
        assert busy.status_code == 409
        assert busy.json() == {"detail": "The agent is already answering.", "code": "busy"}
        assert events(holder["r"])[-1]["code"] == "timeout"  # the first one ended on its own
        assert events(chat(client, "third"))[-1]["type"] == "done"  # and the agent is free again


def test_pre_stream_failures_are_http_statuses_with_no_stream(agent):
    client, _ = agent
    for r in (chat(client, conversation_id="z" * 32), chat(client, "")):
        assert r.status_code in (404, 422)
        assert not r.headers["content-type"].startswith("application/x-ndjson")


def test_an_error_after_streaming_started_is_an_event_not_an_http_error(settings):
    from logpose import ProviderError

    client, _ = build(settings, [Turn.fail(ProviderError("boom", status_code=500))])
    with client:
        r = chat(client)
        assert r.status_code == 200
        got = events(r)
        assert got[0]["type"] == "conversation" and got[-1]["code"] == "provider_error"


def test_conversation_list_detail_and_delete(settings):
    client, _ = build(settings, [Turn.text("One."), Turn.text("Two.")])
    with client:
        first = events(chat(client, "First question"))
        cid = first[0]["id"]
        events(chat(client, "Second question"))
        items = client.get("/api/agent/conversations").json()["items"]
        assert [i["title"] for i in items] == ["Second question", "First question"]
        assert set(items[0]) == {"id", "title", "updated_at", "finding_id", "finding_title"}
        assert items[0]["updated_at"].endswith("Z") and items[0]["finding_id"] is None

        detail = client.get(f"/api/agent/conversations/{cid}").json()
        assert (detail["id"], detail["title"], detail["available"]) == (cid, "First question", True)
        assert (detail["finding_id"], detail["finding_title"]) == (None, None)
        assert [(m["role"], m["text"]) for m in detail["messages"]] == [
            ("user", "First question"),
            ("assistant", "One."),
        ]

        assert client.delete(f"/api/agent/conversations/{cid}").status_code == 204
        assert client.get(f"/api/agent/conversations/{cid}").status_code == 404
        assert client.delete(f"/api/agent/conversations/{cid}").status_code == 404
        assert [i["title"] for i in client.get("/api/agent/conversations").json()["items"]] == [
            "Second question"
        ]


def test_a_conversation_with_no_stored_session_is_listed_but_unavailable(agent):
    client, _ = agent
    from vegapunk.ai.store import add_conversation

    conn = connect(client.app.state.settings.db_path)
    add_conversation(conn, "e" * 32, "Expired one", now())
    conn.close()
    detail = client.get("/api/agent/conversations/" + "e" * 32).json()
    assert detail["available"] is False and detail["messages"] == []


def test_unknown_or_malformed_conversation_ids_are_404(agent):
    client, _ = agent
    for bad in ["z" * 32, "short", "a" * 200]:
        assert client.get(f"/api/agent/conversations/{bad}").status_code == 404
        assert client.delete(f"/api/agent/conversations/{bad}").status_code == 404


def test_title_with_markup_is_returned_as_plain_text(settings):
    client, _ = build(settings, [Turn.text("ok")])
    with client:
        events(chat(client, "<b>x</b> & <script>alert(1)</script>"))
        (item,) = client.get("/api/agent/conversations").json()["items"]
        assert item["title"] == "<b>x</b> & <script>alert(1)</script>"


def test_deleting_while_answering_is_409_busy(settings, monkeypatch):
    monkeypatch.setattr(operator_module, "RUN_TIMEOUT", 1.5)
    client, _ = build(settings, [Turn.text("first"), Turn.hang()])
    with client:
        cid = events(chat(client, "one"))[0]["id"]
        thread = threading.Thread(target=lambda: chat(client, "two", cid))
        thread.start()
        deadline = time.monotonic() + 5
        while not client.app.state.operator._lock.locked():
            assert time.monotonic() < deadline
            time.sleep(0.02)
        r = client.delete(f"/api/agent/conversations/{cid}")
        thread.join(timeout=10)
        assert r.status_code == 409 and r.json()["code"] == "busy"

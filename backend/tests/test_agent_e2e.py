"""The whole stack: Vegapunk over HTTP, real logpose, and a fake model over real HTTP."""

import json
import time
from contextlib import contextmanager

import httpx
import pytest

from tests.agent_helpers import FakeDocker
from tests.conftest import CREDENTIALS
from tests.fake_openai import FakeOpenAI, create_app, free_port, serve
from tests.helpers import listing, now, obs
from vegapunk.ai import operator as operator_module
from vegapunk.ai.tools import TOOL_NAMES
from vegapunk.app import create_app as create_vegapunk
from vegapunk.db import connect

KEY = "sk-e2e-right-key-0001"
PEM = (
    "-----BEGIN OPENSSH PRIVATE KEY-----\n"
    "b3BlbnNzaC1rZXktdjEAAAAABG5vbmUAAAAEbm9uZQAAAAAAAAABAAAAMwAAAAtzc2gtZW\n"
    "QyNTUxOQAAACD5lE1sFjSL9eLwXk3vKQp7P0f1U3qk3W7sXQ8pYb2m8gAAAJgrBWk1KwVp\n"
    "-----END OPENSSH PRIVATE KEY-----"
)


@contextmanager
def stack(settings, fake, *, docker=None, key=KEY, base_url=None):
    """Vegapunk and the fake model running, signed in, with the provider saved."""
    with serve(create_app(fake)) as model_url:
        app = create_vegapunk(settings, docker=docker or FakeDocker())
        with serve(app) as url, httpx.Client(base_url=url, timeout=30) as client:
            assert client.post("/api/setup/admin", json=CREDENTIALS).status_code == 201
            saved = client.put(
                "/api/settings/ai",
                json={
                    "base_url": base_url or f"{model_url}/v1",
                    "model": "test-model",
                    "instructions": "",
                    "api_key": key,
                },
            )
            assert saved.status_code == 200, saved.text
            conn = connect(settings.db_path)
            listing(conn, now(), obs("a", "web"), obs("b", "db", state="exited"))
            conn.close()
            yield client, model_url


def ask(client, message="Is anything wrong?", conversation_id=None):
    with client.stream(
        "POST", "/api/agent/chat", json={"message": message, "conversation_id": conversation_id}
    ) as response:
        assert response.status_code == 200, response.read()
        return [json.loads(line) for line in response.iter_lines() if line.strip()]


def test_end_to_end_tool_call_then_answer(settings):
    fake = FakeOpenAI(
        script=[
            {"tool_calls": [{"id": "call_1", "name": "list_containers", "arguments": {}}]},
            {"text": ["`db` ", "is stopped."]},
        ]
    )
    with stack(settings, fake) as (client, _):
        events = ask(client)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "conversation" and kinds[-1] == "done"
    label = "Read the status of the containers"
    steps = [e for e in events if e["type"] == "step"]
    assert [(s["id"], s["label"], s["status"]) for s in steps] == [
        ("call_1", label, "running"),
        ("call_1", label, "done"),
    ]
    assert "".join(e["text"] for e in events if e["type"] == "text") == "`db` is stopped."
    assert events[-1]["usage"] == {"input_tokens": 20, "output_tokens": 10}

    first, second = fake.requests
    assert first["authorization"] == f"Bearer {KEY}" and first["body"]["model"] == "test-model"
    system = first["body"]["messages"][0]
    assert system["role"] == "system" and "You can only read" in system["content"]
    assert {t["function"]["name"] for t in first["body"]["tools"]} == {
        *TOOL_NAMES,
        "recall_tool_output",
    }
    tool_result = fake.tool_message_texts(1)[0]
    assert "web" in tool_result and "db" in tool_result  # the model was handed the real list


def test_end_to_end_log_secrets_are_redacted_before_they_reach_the_provider(settings):
    logs = [
        "boot ok",
        "db connect password=hunter2 done",
        'client_secret="has spaces inside"',
        *PEM.split("\n"),
        "url postgres://app:pw0rd@db:5432/x",
        "Authorization: Bearer abcdefghijkl123456",
        "after the secrets",
    ]
    fake = FakeOpenAI(
        script=[
            {"tool_calls": [{"id": "L1", "name": "container_logs", "arguments": {"name": "web"}}]},
            {"text": ["Looked."]},
        ]
    )
    with stack(settings, fake, docker=FakeDocker({"a": logs})) as (client, _):
        assert ask(client, "web logs?")[-1]["type"] == "done"
    everything = json.dumps(fake.requests)  # every byte the provider was sent
    for secret in [
        "hunter2",
        "has spaces inside",
        "b3BlbnNz",
        "QyNTUx",
        "5lE1sFjS",
        "pw0rd",
        "abcdefghijkl123456",
    ]:
        assert secret not in everything, secret
    sent = fake.tool_message_texts(1)[0]
    assert "[redacted]" in sent and "boot ok" in sent and "after the secrets" in sent


def test_the_api_key_is_never_sent_anywhere_but_the_provider_and_never_returned(settings):
    fake = FakeOpenAI(script=[{"text": ["Hi."]}])
    with stack(settings, fake) as (client, _):
        events = ask(client)
        saved = client.get("/api/settings/ai").text
    assert KEY not in json.dumps(events) and KEY not in saved
    assert fake.requests[0]["authorization"] == f"Bearer {KEY}"


def test_end_to_end_rejected_key(settings):
    fake = FakeOpenAI(script=[{"text": ["Hello."]}], required_key=KEY)
    with stack(settings, fake, key="sk-wrong-key-00000000") as (client, _):
        events = ask(client)
        assert events[-1] == {
            "type": "error",
            "code": "key_rejected",
            "message": "Your AI provider rejected the API key.",
        }
        assert client.get("/api/settings/ai").json()["key_rejected_at"] is not None
        # saving a working key clears the mark and the agent answers again
        base_url = client.get("/api/settings/ai").json()["base_url"]
        saved = client.put(
            "/api/settings/ai",
            json={"base_url": base_url, "model": "test-model", "instructions": "", "api_key": KEY},
        )
        assert saved.json()["key_rejected_at"] is None
        assert ask(client)[-1]["type"] == "done"


def test_end_to_end_provider_down(settings):
    dead = f"http://127.0.0.1:{free_port()}/v1"
    with stack(settings, FakeOpenAI(), base_url=dead) as (client, _):
        events = ask(client)
    assert events[-1]["code"] == "provider_error"
    assert events[-1]["message"].startswith("The AI provider couldn't answer: ")
    assert KEY not in json.dumps(events)


def test_end_to_end_provider_error_status(settings):
    with stack(settings, FakeOpenAI(script=[{"status": 500}, {"status": 500}])) as (client, _):
        events = ask(client)
    assert events[-1]["code"] == "provider_error"


def test_end_to_end_provider_hangs(settings, monkeypatch):
    monkeypatch.setattr(operator_module, "RUN_TIMEOUT", 1.0)
    with stack(settings, FakeOpenAI(script=[{"hang": True}])) as (client, _):
        events = ask(client)
        assert events[-1]["code"] == "timeout"
        # and the agent is not left stuck
        assert client.post("/api/agent/chat", json={"message": "again"}).status_code in (200, 409)


def test_end_to_end_model_listing_for_the_settings_test(settings):
    fake = FakeOpenAI(required_key=KEY)
    with stack(settings, fake) as (client, model_url):
        good = client.post(
            "/api/settings/ai/test", json={"base_url": f"{model_url}/v1", "api_key": KEY}
        )
        assert good.json() == {"ok": True, "models": ["model-a", "model-b"]}
        saved_key = client.post("/api/settings/ai/test", json={"base_url": f"{model_url}/v1"})
        assert saved_key.json()["ok"] is True  # the saved key is used for the saved URL
        bad = client.post(
            "/api/settings/ai/test",
            json={"base_url": f"{model_url}/v1", "api_key": "sk-wrong-00000000"},
        )
        assert bad.json()["code"] == "key_rejected"
        gone = client.post(
            "/api/settings/ai/test", json={"base_url": f"http://127.0.0.1:{free_port()}/v1"}
        )
        assert gone.json()["code"] == "unreachable"


def test_a_saved_key_is_not_sent_to_a_different_server(settings):
    other = FakeOpenAI(required_key=None)
    with serve(create_app(other)) as other_url:
        with stack(settings, FakeOpenAI()) as (client, _):
            r = client.post("/api/settings/ai/test", json={"base_url": f"{other_url}/v1"})
            assert r.json()["ok"] is True
    # the other server was asked for its models, and the saved key was not attached
    assert other.model_requests == [{"authorization": None}]


def test_closing_the_connection_mid_answer_frees_the_agent(settings):
    fake = FakeOpenAI(
        script=[
            {"text": ["one ", "two ", "three ", "four ", "five"], "chunk_delay": 0.4},
            {"text": ["Back again."]},
        ]
    )
    with stack(settings, fake) as (client, _):
        with client.stream("POST", "/api/agent/chat", json={"message": "tell me a story"}) as r:
            lines = r.iter_lines()
            first = json.loads(next(lines))
            while json.loads(next(lines))["type"] != "text":
                pass
        cid = first["id"]  # leaving the block closes the connection mid-answer
        deadline = time.monotonic() + 8
        while True:
            r = client.post("/api/agent/chat", json={"message": "again", "conversation_id": cid})
            if r.status_code != 409:
                break
            assert time.monotonic() < deadline, "the agent stayed busy after the client left"
            time.sleep(0.2)
        assert r.status_code == 200
        assert json.loads(r.text.splitlines()[-1])["type"] == "done"


def test_the_chat_never_changes_anything_on_the_host(settings):
    """Even if the model asks for a tool that would change something, nothing runs."""
    docker = FakeDocker({"a": ["x"]})
    fake = FakeOpenAI(
        script=[
            {
                "tool_calls": [
                    {"id": "t1", "name": "restart_container", "arguments": {"name": "web"}}
                ]
            },
            {"text": ["I can only read."]},
        ]
    )
    with stack(settings, fake, docker=docker) as (client, _):
        events = ask(client, "restart web")
    assert [e["status"] for e in events if e["type"] == "step"] == ["running", "failed"]
    assert "isn't available" in fake.tool_message_texts(1)[0]
    assert docker.calls == []
    assert events[-1]["type"] == "done"


@pytest.mark.parametrize("n", range(3))
def test_the_stack_is_not_flaky(settings, n):
    fake = FakeOpenAI(
        script=[
            {"tool_calls": [{"id": "x", "name": "host_overview", "arguments": {}}]},
            {"text": ["ok"]},
        ]
    )
    with stack(settings, fake) as (client, _):
        assert ask(client)[-1]["type"] == "done"

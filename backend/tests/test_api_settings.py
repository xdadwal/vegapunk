import asyncio
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from logpose import AuthError, ProviderError

from tests.conftest import CREDENTIALS
from vegapunk.api import settings as settings_api
from vegapunk.app import create_app
from vegapunk.crypto import CryptoError

URL = "https://api.example.com/v1"
SECRET = "sk-SECRETSECRET1234"


class FakeProvider:
    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.closed = False

    async def list_models(self):
        action = self.behaviour.error
        if action is not None:
            if isinstance(action, BaseException):
                raise action
            await action()  # a coroutine function, to simulate a hang
        return list(self.behaviour.models)

    async def aclose(self):
        self.closed = True


@pytest.fixture
def fake():
    return SimpleNamespace(models=["zeta", "alpha", "alpha"], error=None, built=[], providers=[])


@pytest.fixture
def api(settings, fake):
    def make_provider(**kwargs):
        fake.built.append(kwargs)
        provider = FakeProvider(fake)
        fake.providers.append(provider)
        return provider

    with TestClient(create_app(settings, make_provider=make_provider)) as client:
        assert client.post("/api/setup/admin", json=CREDENTIALS).status_code == 201
        yield client


def body(**over):
    data = {"base_url": URL, "model": "gpt-x", "instructions": "", "api_key": SECRET}
    data.update(over)
    return data


def test_settings_require_sign_in(client):
    assert client.get("/api/settings/ai").status_code == 401
    assert client.put("/api/settings/ai", json=body()).status_code == 401
    assert client.delete("/api/settings/ai").status_code == 401
    assert client.post("/api/settings/ai/test", json={"base_url": URL}).status_code == 401


def test_unconfigured_defaults(api):
    assert api.get("/api/settings/ai").json() == {
        "configured": False,
        "base_url": "https://api.openai.com/v1",
        "model": "",
        "instructions": "",
        "key_saved": False,
        "key_hint": None,
        "key_rejected_at": None,
        "key_unreadable": False,
    }


def test_save_then_get_never_returns_the_key(api):
    put = api.put("/api/settings/ai", json=body(instructions="NAS runs the media stack"))
    assert put.status_code == 200
    got = api.get("/api/settings/ai")
    for response in (put, got):
        assert SECRET not in response.text
        assert response.json()["key_hint"] == "…1234"
        assert response.json()["configured"] is True
        assert response.json()["key_saved"] is True
    assert got.json()["instructions"] == "NAS runs the media stack"
    assert (got.json()["base_url"], got.json()["model"]) == (URL, "gpt-x")


def test_the_key_is_stored_encrypted(api, conn):
    api.put("/api/settings/ai", json=body())
    stored = conn.execute("SELECT * FROM ai_settings").fetchone()
    assert SECRET not in "".join(str(v) for v in tuple(stored))


def test_omitted_key_keeps_it_and_remove_key_removes_it(api):
    api.put("/api/settings/ai", json=body())
    kept = api.put("/api/settings/ai", json={"base_url": URL, "model": "gpt-y", "instructions": ""})
    assert kept.json()["key_saved"] is True and kept.json()["model"] == "gpt-y"
    removed = api.put("/api/settings/ai", json=body(api_key=None, remove_key=True))
    assert removed.json()["key_saved"] is False and removed.json()["key_hint"] is None


def test_a_new_key_and_remove_key_together_is_422(api):
    assert api.put("/api/settings/ai", json=body(remove_key=True)).status_code == 422


@pytest.mark.parametrize(
    "over,text",
    [
        ({"base_url": "ftp://x"}, "Base URL"),
        ({"base_url": ""}, "Base URL"),
        ({"model": ""}, "model"),
        ({"instructions": "i" * 4_001}, "Instructions"),
        ({"api_key": "has space"}, "API key"),
        ({"api_key": ""}, "API key"),
    ],
)
def test_invalid_values_are_422_with_a_readable_message(api, over, text):
    r = api.put("/api/settings/ai", json=body(**over))
    assert r.status_code == 422 and text in r.json()["detail"]
    assert api.get("/api/settings/ai").json()["configured"] is False


def test_changing_the_base_url_without_a_new_key_is_422_and_changes_nothing(api):
    api.put("/api/settings/ai", json=body())
    r = api.put(
        "/api/settings/ai",
        json={"base_url": "https://other.example.com/v1", "model": "m", "instructions": ""},
    )
    assert r.status_code == 422
    assert "enter the API key again, or remove the saved key" in r.json()["detail"]
    assert api.get("/api/settings/ai").json()["base_url"] == URL


def test_changing_the_base_url_with_a_new_key_or_remove_key_is_accepted(api):
    api.put("/api/settings/ai", json=body())
    new_key = api.put(
        "/api/settings/ai",
        json=body(base_url="https://b.example.com/v1", api_key="sk-new-key-123456"),
    )
    assert new_key.status_code == 200 and new_key.json()["key_hint"] == "…3456"
    removed = api.put(
        "/api/settings/ai",
        json=body(base_url="http://localhost:11434/v1", api_key=None, remove_key=True),
    )
    assert removed.status_code == 200 and removed.json()["key_saved"] is False


def test_saving_clears_the_rejected_mark(api, conn):
    api.put("/api/settings/ai", json=body())
    conn.execute("UPDATE ai_settings SET key_rejected_at = '2026-01-01T15:12:00+00:00'")
    assert api.get("/api/settings/ai").json()["key_rejected_at"] == "2026-01-01T15:12:00Z"
    assert (
        api.put("/api/settings/ai", json=body(api_key=None, remove_key=False)).json()[
            "key_rejected_at"
        ]
        is None
    )


def test_delete_turns_the_agent_off_and_is_repeatable(api):
    api.put("/api/settings/ai", json=body())
    assert api.delete("/api/settings/ai").status_code == 204
    assert api.get("/api/settings/ai").json()["configured"] is False
    assert api.delete("/api/settings/ai").status_code == 204


def test_test_connection_lists_models_sorted_and_closes_the_provider(api, fake):
    r = api.post("/api/settings/ai/test", json={"base_url": URL, "api_key": "sk-typed-key-00001"})
    assert r.json() == {"ok": True, "models": ["alpha", "zeta"]}
    assert fake.built == [{"base_url": URL, "api_key": "sk-typed-key-00001"}]
    assert all(p.closed for p in fake.providers)


def test_test_caps_the_model_list(api, fake):
    fake.models = [f"m{i:04d}" for i in range(500)]
    r = api.post("/api/settings/ai/test", json={"base_url": URL})
    assert len(r.json()["models"]) == 200


def test_test_uses_the_saved_key_only_for_the_saved_base_url(api, fake):
    api.put("/api/settings/ai", json=body())
    api.post("/api/settings/ai/test", json={"base_url": URL})
    api.post("/api/settings/ai/test", json={"base_url": URL + "/"})
    assert [b["api_key"] for b in fake.built] == [SECRET, SECRET]


def test_test_sends_no_key_to_a_different_base_url(api, fake):
    api.put("/api/settings/ai", json=body())
    api.post("/api/settings/ai/test", json={"base_url": "https://other.example.com/v1"})
    assert fake.built == [{"base_url": "https://other.example.com/v1", "api_key": ""}]


def test_test_with_nothing_saved_sends_no_key(api, fake):
    api.post("/api/settings/ai/test", json={"base_url": "http://localhost:11434/v1"})
    assert fake.built[0]["api_key"] == ""


def test_test_rejects_a_bad_url_without_building_a_provider(api, fake):
    r = api.post("/api/settings/ai/test", json={"base_url": "nonsense"})
    assert r.status_code == 200 and r.json()["ok"] is False and "Base URL" in r.json()["message"]
    assert fake.built == []


def test_test_reports_a_rejected_key(api, fake):
    fake.error = AuthError("authentication failed")
    r = api.post("/api/settings/ai/test", json={"base_url": URL, "api_key": "sk-typed-key-00001"})
    assert r.json() == {
        "ok": False,
        "code": "key_rejected",
        "message": "The server rejected the API key.",
    }


def test_test_reports_unreachable_timeout_and_http_errors(api, fake, monkeypatch):
    fake.error = ProviderError(
        "connection refused"
    )  # no HTTP status: the server could not be reached
    unreachable = api.post("/api/settings/ai/test", json={"base_url": URL}).json()
    assert unreachable == {"ok": False, "code": "unreachable", "message": f"Couldn't reach {URL}."}

    async def hang():
        await asyncio.sleep(30)

    monkeypatch.setattr(settings_api, "TEST_TIMEOUT", 0.05)
    fake.error = hang
    assert api.post("/api/settings/ai/test", json={"base_url": URL}).json()["code"] == "unreachable"

    fake.error = ProviderError("request failed with status 404: not found", status_code=404)
    missing = api.post("/api/settings/ai/test", json={"base_url": URL}).json()
    assert missing["code"] == "error" and missing["message"].startswith(
        "Check the Base URL; it usually ends in /v1."
    )

    fake.error = ProviderError("boom", status_code=500)
    assert api.post("/api/settings/ai/test", json={"base_url": URL}).json()["code"] == "error"
    fake.error = RuntimeError("something odd")
    assert (
        "something odd"
        in api.post("/api/settings/ai/test", json={"base_url": URL}).json()["message"]
    )


def test_test_never_echoes_the_key_or_its_prefix_in_a_message(api, fake):
    key = "sk-proj-abcdefghijklmnop"
    fake.error = ProviderError(f"bad key {key} / <redacted sk-pro… len=24>", status_code=403)
    r = api.post("/api/settings/ai/test", json={"base_url": URL, "api_key": key})
    assert key not in r.text and "sk-pro" not in r.text


def test_successful_test_of_the_saved_settings_clears_the_rejected_mark(api, conn):
    api.put("/api/settings/ai", json=body())
    conn.execute("UPDATE ai_settings SET key_rejected_at = '2026-01-01T15:12:00+00:00'")
    assert api.post("/api/settings/ai/test", json={"base_url": URL}).json()["ok"] is True
    assert api.get("/api/settings/ai").json()["key_rejected_at"] is None


def test_a_test_of_another_server_does_not_clear_the_mark(api, conn):
    api.put("/api/settings/ai", json=body())
    conn.execute("UPDATE ai_settings SET key_rejected_at = '2026-01-01T15:12:00+00:00'")
    api.post("/api/settings/ai/test", json={"base_url": "https://other.example.com/v1"})
    assert api.get("/api/settings/ai").json()["key_rejected_at"] is not None


def test_an_unreadable_saved_key_is_reported(api, settings):
    api.put("/api/settings/ai", json=body())
    (settings.data_dir / "secret.key").write_bytes(b"x" * 44)  # not the key that encrypted it...
    # ...but the running app holds the real key in memory, so replace what the app uses
    from cryptography.fernet import Fernet

    api.app.state.crypto_key = Fernet.generate_key()
    got = api.get("/api/settings/ai").json()
    assert got["key_unreadable"] is True and got["key_saved"] is True and got["key_hint"] == "…1234"


def test_a_corrupt_key_file_stops_startup_with_a_clear_message(settings):
    settings.data_dir.mkdir(parents=True)
    (settings.data_dir / "secret.key").write_text("garbage")
    with pytest.raises(CryptoError, match="secret.key"):
        with TestClient(create_app(settings)):
            pass

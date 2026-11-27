from datetime import UTC, datetime

import pytest

from tests.conftest import CREDENTIALS
from vegapunk.db import connect
from vegapunk.watch import findings

T = datetime(2026, 11, 27, 9, 0, tzinfo=UTC)
KEY = "sk-test-key-abcdef123456"
BUSY = "Another check is running. Try again in a moment."


class FakeRunner:
    def __init__(self, skipped=None, running=False, started=True, error=None):
        self.skipped, self.running, self.started, self.error = skipped, running, started, error
        self.calls = 0

    async def status(self):
        return {
            "running": self.running,
            "last_run_at": "2026-11-27T03:00:00Z",
            "last_error": self.error,
            "skipped": self.skipped,
        }

    async def run_check_now(self):
        self.calls += 1
        return self.started


def draft(subject="web", title="Web is down", evidence=()):
    return findings.FindingDraft(subject, title, "Diagnosis.", "Restart it.", tuple(evidence))


@pytest.fixture
def api(client, settings):
    assert client.post("/api/setup/admin", json=CREDENTIALS).status_code == 201
    client.headers["X-Requested-With"] = "vegapunk"
    client.runner = FakeRunner()
    client.app.state.runner = client.runner
    conn = connect(settings.db_path)
    client.conn = conn
    yield client
    client.app.state.runner = None
    conn.close()


def add(api, key="k1", trigger="scheduled", **kw):
    return findings.upsert_finding(api.conn, draft(**kw), key=key, trigger=trigger, now=T)


def test_routes_require_sign_in(client):
    for method, url in [
        ("get", "/api/findings"),
        ("get", "/api/findings/1"),
        ("post", "/api/findings/1/review"),
        ("post", "/api/findings/check"),
        ("get", "/api/settings/checks"),
        ("put", "/api/settings/checks"),
    ]:
        assert getattr(client, method)(url).status_code == 401


def test_list_filters_and_counts(api):
    a = add(api, key="a", subject="web", title="Web is down")
    add(api, key="b", subject="db", title="Disk full", trigger="watch")
    findings.review(api.conn, a, T)
    body = api.get("/api/findings").json()
    assert body["open_count"] == 1
    assert len(body["items"]) == 2
    item = next(i for i in body["items"] if i["key"] == "b")
    assert item["trigger"] == "watch" and item["first_seen_at"].endswith("Z")
    assert "diagnosis" not in item
    assert [i["key"] for i in api.get("/api/findings?status=reviewed").json()["items"]] == ["a"]
    assert [i["key"] for i in api.get("/api/findings?q=DISK").json()["items"]] == ["b"]
    assert api.get("/api/findings?status=bogus").status_code == 422


def test_list_reports_checks(api):
    findings.record_investigation(api.conn, "scheduled:check", "scheduled", T, "nothing")
    checks = api.get("/api/findings").json()["checks"]
    assert checks == {
        "enabled": True,
        "hour": checks["hour"],
        "watch_enabled": True,
        "last_run_at": "2026-11-27T03:00:00Z",
        "last_error": None,
        "skipped": None,
        "running": False,
    }
    api.runner.skipped, api.runner.error = "key_rejected", "boom"
    c = api.get("/api/findings").json()["checks"]
    assert c["skipped"] == "key_rejected" and c["last_error"] == "boom"


def test_list_reports_the_watch_switch_with_or_without_a_runner(api):
    findings.save_check_settings(api.conn, True, 2, watch_enabled=False)
    assert api.get("/api/findings").json()["checks"]["watch_enabled"] is False
    api.app.state.runner = None
    assert api.get("/api/findings").json()["checks"]["watch_enabled"] is False


def test_last_error_hidden_while_running(api):
    api.runner.running, api.runner.error = True, "The check was interrupted."
    c = api.get("/api/findings").json()["checks"]
    assert c["running"] is True and c["last_error"] is None


def test_last_run_at_is_null_until_a_check_has_run(api):
    # A fresh install stores a placeholder time; it is not a check that ran.
    findings.set_check_last_run(api.conn, T)
    assert api.get("/api/findings").json()["checks"]["last_run_at"] is None
    api.app.state.runner = None
    assert api.get("/api/findings").json()["checks"]["last_run_at"] is None
    findings.record_investigation(api.conn, "scheduled:check", "scheduled", T, "nothing")
    assert api.get("/api/findings").json()["checks"]["last_run_at"] == "2026-11-27T09:00:00Z"


@pytest.mark.parametrize("path", ["/api/findings/check", "/api/findings/{fid}/review"])
def test_body_less_posts_need_the_request_header(api, path):
    url = path.format(fid=add(api))
    del api.headers["X-Requested-With"]
    for headers in ({}, {"X-Requested-With": "XMLHttpRequest"}):
        r = api.post(url, headers=headers)
        assert r.status_code == 403 and r.json() == {"detail": "Missing request header."}
    assert api.runner.calls == 0
    r = api.post(url, headers={"X-Requested-With": "vegapunk"})
    assert r.status_code in (202, 204)


def test_list_without_runner_reads_the_database(api):
    api.app.state.runner = None
    findings.set_check_error(api.conn, "oops")
    c = api.get("/api/findings").json()["checks"]
    assert c["skipped"] is None and c["running"] is False and c["last_error"] == "oops"


def test_detail_has_evidence(api):
    ev = findings.EvidenceLine("log", "line", "2026-11-27T08:00:00Z")
    fid = add(api, evidence=[ev, findings.EvidenceLine("metric", "cpu 99", None)])
    body = api.get(f"/api/findings/{fid}").json()
    assert body["diagnosis"] == "Diagnosis." and body["suggested_fix"] == "Restart it."
    assert body["evidence"] == [
        {"source": "log", "text": "line", "at": "2026-11-27T08:00:00Z"},
        {"source": "metric", "text": "cpu 99", "at": None},
    ]
    assert body["status"] == "open" and body["reviewed_at"] is None


@pytest.mark.parametrize("fid", ["999", "abc", "0", "-1", "1.5", "99999999999999999999"])
def test_unknown_or_bad_ids_are_404(api, fid):
    for r in (api.get(f"/api/findings/{fid}"), api.post(f"/api/findings/{fid}/review")):
        assert r.status_code == 404
        assert r.json() == {"detail": "No such finding."}


def test_review_is_idempotent_and_keeps_resolved(api):
    fid = add(api)
    assert api.post(f"/api/findings/{fid}/review").status_code == 204
    assert api.post(f"/api/findings/{fid}/review").status_code == 204
    assert api.get(f"/api/findings/{fid}").json()["status"] == "reviewed"
    other = add(api, key="z")
    findings.resolve(api.conn, "z", T)
    assert api.post(f"/api/findings/{other}/review").status_code == 204
    assert api.get(f"/api/findings/{other}").json()["status"] == "resolved"


def test_markup_titles_come_back_exact(api):
    title = "<img src=x onerror=alert(1)>"
    fid = add(api, title=title)
    assert api.get(f"/api/findings/{fid}").json()["title"] == title
    assert api.get("/api/findings").json()["items"][0]["title"] == title


def test_check_started(api):
    r = api.post("/api/findings/check")
    assert r.status_code == 202 and r.json() == {"started": True}
    assert api.runner.calls == 1


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"skipped": "off"}, "Connect an AI provider first."),
        ({"running": True}, BUSY),
        ({"started": False}, BUSY),
        ({"skipped": "key_rejected"}, "Your AI provider rejected the API key."),
        (
            {"skipped": "unsupported"},
            "Your AI provider can't give structured answers, so checks can't run.",
        ),
    ],
)
def test_check_conflicts(api, kwargs, message):
    api.app.state.runner = FakeRunner(**kwargs)
    r = api.post("/api/findings/check")
    assert r.status_code == 409 and r.json() == {"detail": message}


def test_check_without_runner(api):
    api.app.state.runner = None
    r = api.post("/api/findings/check")
    assert r.status_code == 409
    assert r.json() == {"detail": "Checks aren't running on this server."}


def test_check_settings_round_trip(api):
    assert api.get("/api/settings/checks").json() == {
        "enabled": True,
        "hour": 2,
        "watch_enabled": True,
    }
    saved = {"enabled": False, "hour": 5, "watch_enabled": False}
    r = api.put("/api/settings/checks", json=saved)
    assert r.status_code == 200 and r.json() == saved
    assert api.get("/api/settings/checks").json() == saved
    again = {"enabled": True, "hour": 5, "watch_enabled": True}
    assert api.put("/api/settings/checks", json=again).json() == again


@pytest.mark.parametrize(
    "body",
    [
        {"enabled": True, "hour": 24, "watch_enabled": True},
        {"enabled": True, "hour": -1, "watch_enabled": True},
        {"enabled": True, "hour": True, "watch_enabled": True},
        {"enabled": True, "hour": "5", "watch_enabled": True},
        {"enabled": "yes", "hour": 5, "watch_enabled": True},
        {"enabled": True, "watch_enabled": True},
        {"enabled": True, "hour": 5},
        {"enabled": True, "hour": 5, "watch_enabled": 1},
        {"enabled": True, "hour": 5, "watch_enabled": "true"},
        {"enabled": True, "hour": 5, "watch_enabled": None},
    ],
)
def test_check_settings_rejects_bad_input(api, body):
    assert api.put("/api/settings/checks", json=body).status_code == 422


def test_responses_never_carry_the_key(api):
    body = {"base_url": "https://api.example.com/v1", "model": "m", "api_key": KEY}
    assert api.put("/api/settings/ai", json=body).status_code == 200
    hint = api.get("/api/settings/ai").json()["key_hint"]
    fid = add(api)
    texts = [
        api.get("/api/findings").text,
        api.get(f"/api/findings/{fid}").text,
        api.get("/api/settings/checks").text,
        api.post("/api/findings/check").text,
    ]
    for t in texts:
        assert KEY not in t and (not hint or hint not in t)


def test_list_database_work_is_off_the_event_loop(api, monkeypatch):
    import threading

    add(api)
    seen = []
    real = findings.list_findings

    def spy(*a, **kw):
        seen.append(threading.current_thread())
        return real(*a, **kw)

    monkeypatch.setattr(findings, "list_findings", spy)
    assert api.get("/api/findings").status_code == 200
    assert seen and seen[0] is not threading.main_thread()
    # the TestClient portal runs the loop in its own thread: the work must not be on it either
    assert all(t.name != "anyio-portal" and "portal" not in t.name.lower() for t in seen)


def test_huge_id_is_404(api):
    huge = "9" * 5000
    assert api.get(f"/api/findings/{huge}").status_code == 404
    assert api.post(f"/api/findings/{huge}/review").status_code == 404

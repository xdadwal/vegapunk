"""Asking the agent about a finding: one conversation per finding, with its context."""

import asyncio
import json
from datetime import UTC, datetime

import pytest

from tests.agent_helpers import FakeProvider, Turn, collect, make_operator, save_config
from tests.fake_openai import FakeOpenAI
from tests.helpers import listing, now, obs
from tests.test_agent_e2e import KEY, stack
from vegapunk.ai.operator import UnknownFinding
from vegapunk.ai.store import add_conversation, list_conversations
from vegapunk.db import connect
from vegapunk.watch import findings

T = datetime(2026, 11, 27, 15, 12, tzinfo=UTC)
DIAGNOSIS = "It runs out of memory and is killed about every 10 minutes."
LINES = [
    findings.EvidenceLine("log", "FATAL: out of memory, killing worker 3", "2026-11-27T15:11:58Z"),
    findings.EvidenceLine("metric", "Memory at 99% of its 512 MiB limit for 10 min", None),
]
SECTION = "This conversation is about one finding that Vegapunk raised."


def add_finding(db_path, title="Web keeps restarting", diagnosis=DIAGNOSIS, key="c:web"):
    conn = connect(db_path)
    try:
        draft = findings.FindingDraft("web", title, diagnosis, "Raise its memory limit.", (*LINES,))
        return findings.upsert_finding(conn, draft, key=key, trigger="watch", now=T)
    finally:
        conn.close()


def db(db_path, fn):
    conn = connect(db_path)
    try:
        return fn(conn)
    finally:
        conn.close()


def rows(db_path):
    return db(db_path, lambda c: c.execute("SELECT * FROM conversations").fetchall())


def system_of(request) -> str:
    message = request["body"]["messages"][0]
    assert message["role"] == "system"
    return message["content"]


def ask(client, message="What is going on?", conversation_id=None, finding_id=None):
    body = {"message": message, "conversation_id": conversation_id, "finding_id": finding_id}
    r = client.post("/api/agent/chat", json=body)
    assert r.status_code == 200, r.text
    return [json.loads(line) for line in r.text.splitlines() if line.strip()]


# ---- the operator alone
@pytest.fixture
def ready(settings, db_path):
    save_config(settings)
    conn = connect(db_path)
    listing(conn, now(), obs("a", "web"))
    conn.close()
    return settings


def test_an_unknown_finding_raises_before_any_event_and_creates_nothing(ready):
    provider = FakeProvider([Turn.text("x")])
    op = make_operator(ready, provider)

    async def go():
        gen = op.chat("hello", None, 999)
        with pytest.raises(UnknownFinding):
            await gen.__anext__()

    asyncio.run(go())
    assert rows(ready.db_path) == [] and provider.requests == []
    assert not op._lock.locked()


def test_the_conversation_event_names_its_finding(ready):
    fid = add_finding(ready.db_path)
    provider = FakeProvider([Turn.text("one"), Turn.text("two")])
    op = make_operator(ready, provider)
    linked = asyncio.run(collect(op, "Why?", None, fid))[0]
    plain = asyncio.run(collect(op, "Anything wrong?"))[0]
    assert linked == {
        "type": "conversation",
        "id": linked["id"],
        "title": "About: Web keeps restarting",
        "finding_id": fid,
    }
    assert plain["finding_id"] is None and plain["title"] == "Anything wrong?"


def test_a_long_finding_title_is_cut_like_any_title(ready):
    fid = add_finding(ready.db_path, title="Web " + "very " * 30 + "slow")
    op = make_operator(ready, FakeProvider([Turn.text("ok")]))
    title = asyncio.run(collect(op, "Why?", None, fid))[0]["title"]
    assert title.startswith("About: Web very") and title.endswith("…") and len(title) == 60


def test_a_finding_pruned_after_the_check_but_before_the_insert_is_unknown(ready, monkeypatch):
    fid = add_finding(ready.db_path)
    op = make_operator(ready, FakeProvider([Turn.text("x")]))
    real = op._open_finding_conversation

    def prune_first(conn, finding_id, title):
        conn.execute("DELETE FROM findings WHERE id = ?", (finding_id,))
        return real(conn, finding_id, title)

    monkeypatch.setattr(op, "_open_finding_conversation", prune_first)

    async def go():
        gen = op.chat("hello", None, fid)
        with pytest.raises(UnknownFinding):
            await gen.__anext__()

    asyncio.run(go())
    assert rows(ready.db_path) == []


def test_a_conversation_started_meanwhile_for_the_finding_is_the_one_used(ready, monkeypatch):
    """The finding had no conversation when it was looked up, but one exists by the time
    of the insert: that one continues, and no second row appears."""
    from vegapunk.ai import store

    fid = add_finding(ready.db_path)
    db(ready.db_path, lambda c: add_conversation(c, "m" * 32, "About: earlier", T, finding_id=fid))
    real = store.conversation_for_finding
    calls = []

    def missing_at_first(conn, finding_id):
        calls.append(finding_id)
        return None if len(calls) == 1 else real(conn, finding_id)

    monkeypatch.setattr(store, "conversation_for_finding", missing_at_first)
    op = make_operator(ready, FakeProvider([Turn.text("ok")]))
    got = asyncio.run(collect(op, "Why?", None, fid))
    assert len(calls) == 2  # the lookup missed it, the fallback found it
    assert got[0]["id"] == "m" * 32 and got[0]["finding_id"] == fid
    assert got[-1]["type"] == "done"
    assert [r["id"] for r in rows(ready.db_path)] == ["m" * 32]


# ---- the whole stack, with a fake model over HTTP
def test_the_finding_is_in_the_system_prompt_of_every_answer_and_kept_fresh(settings):
    fake = FakeOpenAI(script=[{"text": ["First."]}, {"text": ["Second."]}])
    with stack(settings, fake) as (client, _):
        fid = add_finding(settings.db_path)
        first = ask(client, "Why does web restart?", finding_id=fid)
        cid = first[0]["id"]
        assert first[0]["finding_id"] == fid and first[-1]["type"] == "done"
        findings.review(connect(settings.db_path), fid, T)
        second = ask(client, "And now?", conversation_id=cid)
        assert second[0] == {**first[0], "type": "conversation"} and second[-1]["type"] == "done"

    one, two = (system_of(r) for r in fake.requests)
    for prompt in (one, two):
        assert SECTION in prompt and DIAGNOSIS in prompt
        assert "FATAL: out of memory, killing worker 3" in prompt
        assert "Memory at 99% of its 512 MiB limit for 10 min" in prompt
    assert "Status: Needs you" in one and "Status: Reviewed" in two
    # the second answer continues the same session
    assert [m["role"] for m in fake.requests[1]["body"]["messages"]][1:] == [
        "user",
        "assistant",
        "user",
    ]


def test_asking_about_the_same_finding_again_resumes_its_conversation(settings):
    fake = FakeOpenAI(script=[{"text": ["One."]}, {"text": ["Two."]}, {"text": ["Three."]}])
    with stack(settings, fake) as (client, _):
        fid = add_finding(settings.db_path)
        a = ask(client, "First look", finding_id=fid)[0]["id"]
        b = ask(client, "Second look", finding_id=fid)[0]["id"]
        other = ask(client, "Unrelated")[0]["id"]
        assert a == b != other
        linked = [r for r in rows(settings.db_path) if r["finding_id"] == fid]
        assert len(linked) == 1 and len(rows(settings.db_path)) == 2
        texts = [m["content"] for m in fake.requests[1]["body"]["messages"] if m["role"] == "user"]
        assert texts == ["First look", "Second look"]

        # deleting it lets the next question start a fresh one
        assert client.delete(f"/api/agent/conversations/{a}").status_code == 204
        c = ask(client, "Third look", finding_id=fid)[0]["id"]
        assert c not in (a, other)


def test_an_unknown_finding_is_404_and_creates_no_conversation(settings):
    with stack(settings, FakeOpenAI(script=[{"text": ["x"]}])) as (client, _):
        r = client.post("/api/agent/chat", json={"message": "hi", "finding_id": 424242})
        assert r.status_code == 404
        assert r.json() == {"detail": "That finding no longer exists."}
        assert client.get("/api/agent/conversations").json() == {"items": []}
    assert rows(settings.db_path) == []


@pytest.mark.parametrize("bad", [0, -1, "1", 1.5, True, 2**63, [1], {"id": 1}])
def test_a_malformed_finding_id_is_422(settings, bad):
    fake = FakeOpenAI(script=[{"text": ["x"]}])
    with stack(settings, fake) as (client, _):
        r = client.post("/api/agent/chat", json={"message": "hi", "finding_id": bad})
        assert r.status_code == 422
    assert fake.requests == [] and rows(settings.db_path) == []


def test_a_pruned_finding_leaves_a_conversation_that_still_answers_without_context(settings):
    fake = FakeOpenAI(script=[{"text": ["One."]}, {"text": ["Two."]}])
    with stack(settings, fake) as (client, _):
        fid = add_finding(settings.db_path)
        cid = ask(client, "Why?", finding_id=fid)[0]["id"]
        db(settings.db_path, lambda c: c.execute("DELETE FROM findings WHERE id = ?", (fid,)))
        events = ask(client, "Still there?", conversation_id=cid)
        assert events[0]["finding_id"] is None and events[-1]["type"] == "done"
        detail = client.get(f"/api/agent/conversations/{cid}").json()
        assert (detail["finding_id"], detail["finding_title"]) == (None, None)
    assert SECTION in system_of(fake.requests[0])
    assert SECTION not in system_of(fake.requests[1])


def test_a_plain_conversation_ignores_a_finding_id_in_the_body(settings):
    fake = FakeOpenAI(script=[{"text": ["One."]}, {"text": ["Two."]}])
    with stack(settings, fake) as (client, _):
        fid = add_finding(settings.db_path)
        cid = ask(client, "Anything wrong?")[0]["id"]
        events = ask(client, "And this?", conversation_id=cid, finding_id=fid)
        assert events[0]["finding_id"] is None
        assert client.get(f"/api/findings/{fid}").json()["conversation_id"] is None
    assert SECTION not in system_of(fake.requests[1])
    assert [r["finding_id"] for r in rows(settings.db_path)] == [None]


def test_a_finding_conversation_keeps_its_own_finding_whatever_the_body_says(settings):
    fake = FakeOpenAI(script=[{"text": ["One."]}, {"text": ["Two."]}])
    with stack(settings, fake) as (client, _):
        fid = add_finding(settings.db_path)
        other = add_finding(settings.db_path, title="Disk filling up", diagnosis="Disk.", key="d")
        cid = ask(client, "Why?", finding_id=fid)[0]["id"]
        events = ask(client, "More?", conversation_id=cid, finding_id=other)
        assert events[0]["finding_id"] == fid
    second = system_of(fake.requests[1])
    assert DIAGNOSIS in second and "Disk filling up" not in second


def test_the_api_exposes_the_link_both_ways(settings):
    fake = FakeOpenAI(script=[{"text": ["One."]}, {"text": ["Two."]}])
    with stack(settings, fake) as (client, _):
        client.headers["X-Requested-With"] = "vegapunk"
        fid = add_finding(settings.db_path)
        other = add_finding(settings.db_path, title="Disk filling up", key="d")
        assert client.get(f"/api/findings/{fid}").json()["conversation_id"] is None
        items = client.get("/api/findings").json()["items"]
        assert [i["conversation_id"] for i in items] == [None, None]

        cid = ask(client, "Why?", finding_id=fid)[0]["id"]
        ask(client, "Plain question")

        assert client.get(f"/api/findings/{fid}").json()["conversation_id"] == cid
        assert client.get(f"/api/findings/{other}").json()["conversation_id"] is None
        by_id = {i["id"]: i for i in client.get("/api/findings").json()["items"]}
        assert by_id[fid]["conversation_id"] == cid and by_id[other]["conversation_id"] is None

        listed = {i["id"]: i for i in client.get("/api/agent/conversations").json()["items"]}
        assert set(listed[cid]) == {"id", "title", "updated_at", "finding_id", "finding_title"}
        assert (listed[cid]["finding_id"], listed[cid]["finding_title"]) == (
            fid,
            "Web keeps restarting",
        )
        (plain,) = [i for i in listed.values() if i["id"] != cid]
        assert (plain["finding_id"], plain["finding_title"]) == (None, None)

        detail = client.get(f"/api/agent/conversations/{cid}").json()
        assert (detail["finding_id"], detail["finding_title"]) == (fid, "Web keeps restarting")
        assert detail["available"] is True and len(detail["messages"]) == 2


def test_the_api_key_goes_only_in_the_provider_auth_header(settings):
    fake = FakeOpenAI(script=[{"text": ["One."]}, {"text": ["Two."]}])
    with stack(settings, fake) as (client, _):
        fid = add_finding(settings.db_path)
        responses = [json.dumps(ask(client, "Why?", finding_id=fid))]
        cid = json.loads(responses[0])[0]["id"]
        responses.append(json.dumps(ask(client, "More?", conversation_id=cid)))
        for url in ("/api/findings", f"/api/findings/{fid}", "/api/agent/conversations"):
            responses.append(client.get(url).text)
        responses.append(client.get(f"/api/agent/conversations/{cid}").text)
    for text in responses:
        assert KEY not in text
    assert KEY not in json.dumps([r["body"] for r in fake.requests])
    assert all(r["authorization"] == f"Bearer {KEY}" for r in fake.requests)


def test_conversations_from_before_the_link_still_list(db_path):
    db(db_path, lambda c: add_conversation(c, "o" * 32, "Old", T))
    (item,) = db(db_path, list_conversations)
    assert (item["finding_id"], item["finding_title"]) == (None, None)

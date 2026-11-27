"""Answers run in a task of their own: they outlive whoever is reading them, any number of
readers can follow one, and only an explicit stop (or shutdown) cancels one."""

import asyncio
import logging

import pytest

from tests.agent_helpers import FakeProvider, Turn, make_operator, save_config
from tests.helpers import listing, now, obs
from vegapunk.ai import operator as operator_module
from vegapunk.ai.operator import (
    Answer,
    OperatorBusy,
    OperatorOff,
    UnknownConversation,
    UnknownFinding,
)
from vegapunk.ai.store import list_conversations
from vegapunk.db import connect

STOPPED = {"type": "error", "code": "stopped", "message": "Stopped."}
PROBLEM = {"type": "error", "code": "error", "message": "The agent hit a problem."}


@pytest.fixture
def ready(settings, db_path):
    save_config(settings)
    conn = connect(db_path)
    listing(conn, now(), obs("a", "web"))
    conn.close()
    return settings


def run(coro):
    return asyncio.run(coro)


def types(events):
    return [e["type"] for e in events]


def text_of(events):
    return "".join(e["text"] for e in events if e["type"] == "text")


async def until(condition, timeout=5.0):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.005)


async def follow(answer: Answer) -> list[dict]:
    return [e async for e in answer.subscribe()]


def rows(settings):
    conn = connect(settings.db_path)
    try:
        return list_conversations(conn)
    finally:
        conn.close()


def test_the_checks_still_fail_before_an_answer_starts(ready, settings):
    op = make_operator(ready, FakeProvider([Turn.text("x")]))

    async def go():
        with pytest.raises(UnknownConversation):
            await op.start_answer("hi", "z" * 32)
        with pytest.raises(UnknownFinding):
            await op.start_answer("hi", None, 999)
        assert not op._lock.locked() and op.active("z" * 32) is None

    run(go())
    assert rows(ready) == []


def test_no_provider_is_operator_off(settings, db_path):
    connect(db_path).close()
    op = make_operator(settings, FakeProvider([]))

    async def go():
        with pytest.raises(OperatorOff):
            await op.start_answer("hi", None)

    run(go())


def test_an_answer_starts_with_its_first_event_and_runs_with_nobody_reading(ready):
    provider = FakeProvider([Turn.text("All good.", chunks=["All ", "good."])])
    op = make_operator(ready, provider)

    async def go():
        answer = await op.start_answer("Anything wrong?", None)
        assert types(answer.events) == ["conversation"]
        cid = answer.conversation_id
        assert answer.events[0]["id"] == cid
        assert op.active(cid) is answer and not answer.done
        await asyncio.wait_for(answer.wait(), 5)
        assert answer.done and op.active(cid) is None and not op._lock.locked()
        # Adjacent text is kept as one event.
        assert types(answer.events) == ["conversation", "text", "done"]
        assert text_of(answer.events) == "All good."
        return cid, await op.history(cid)

    cid, history = run(go())
    assert [(m["role"], m["text"]) for m in history] == [
        ("user", "Anything wrong?"),
        ("assistant", "All good."),
    ]


def test_a_second_answer_while_one_runs_is_busy(ready):
    gate = asyncio.Event()

    async def go():
        op = make_operator(ready, FakeProvider([Turn.gated(["a", "b"], gate, 1), Turn.text("c")]))
        answer = await op.start_answer("one", None)
        with pytest.raises(OperatorBusy):
            await op.start_answer("two", None)
        with pytest.raises(OperatorBusy):
            await op.delete(answer.conversation_id)
        gate.set()
        await answer.wait()
        third = await op.start_answer("three", None)
        await third.wait()
        return third.events

    assert types(run(go()))[-1] == "done"


def test_a_reader_joining_late_gets_what_was_said_so_far_then_follows(ready):
    async def go():
        gate = asyncio.Event()
        op = make_operator(ready, FakeProvider([Turn.gated(["one ", "two ", "three"], gate, 2)]))
        answer = await op.start_answer("Tell me", None)
        await until(lambda: text_of(answer.events) == "one two ")
        early = answer.subscribe()
        replayed = [await early.__anext__(), await early.__anext__()]
        late = asyncio.create_task(follow(answer))
        gate.set()
        rest = [e async for e in early]
        return replayed, rest, await late

    replayed, rest, late = run(go())
    assert replayed[0]["type"] == "conversation"
    assert replayed[1] == {"type": "text", "text": "one two "}  # coalesced on replay
    assert text_of(replayed + rest) == "one two three" and rest[-1]["type"] == "done"
    assert text_of(late) == "one two three" and late[-1]["type"] == "done"
    assert types(late).count("conversation") == 1


def test_a_reader_leaving_does_not_affect_the_answer(ready):
    async def go():
        gate = asyncio.Event()
        op = make_operator(ready, FakeProvider([Turn.gated(["a", "b"], gate, 1)]))
        answer = await op.start_answer("hi", None)
        reader = answer.subscribe()
        await reader.__anext__()
        await reader.aclose()
        assert not answer._followers
        gate.set()
        await asyncio.wait_for(answer.wait(), 5)
        return answer.events, await follow(answer)

    events, after = run(go())
    assert types(events) == ["conversation", "text", "done"] and text_of(events) == "ab"
    assert after == events  # a reader after the end replays it all and stops


def test_stop_cancels_once_keeps_the_question_and_frees_the_agent(ready):
    async def go():
        provider = FakeProvider([Turn.hang(), Turn.text("Fine.")])
        op = make_operator(ready, provider)
        answer = await op.start_answer("first question", None)
        cid = answer.conversation_id
        reader = asyncio.create_task(follow(answer))
        await until(lambda: len(provider.requests) == 1)
        assert await op.stop(cid) is True
        assert answer.done and not op._lock.locked() and op.active(cid) is None
        assert await op.stop(cid) is False  # nothing runs now
        seen = await reader
        history = await op.history(cid)
        again = await op.start_answer("second question", cid)
        await again.wait()
        return seen, history, again.events

    seen, history, again = run(go())
    assert seen[-1] == STOPPED and types(seen) == ["conversation", "error"]
    assert [m["role"] for m in history] == ["user"]
    assert again[-1]["type"] == "done"


def test_stop_when_nothing_runs_is_false(ready):
    op = make_operator(ready, FakeProvider([]))
    assert run(op.stop("a" * 32)) is False
    assert run(op.stop("not an id")) is False


def test_an_unexpected_error_ends_the_answer_with_an_error_event(ready, caplog):
    op = make_operator(ready, FakeProvider([]))

    async def broken(message, conversation_id, finding_id=None):
        yield {"type": "conversation", "id": "b" * 32, "title": "x", "finding_id": None}
        raise RuntimeError("unexpected")

    op.chat = broken

    async def go():
        answer = await op.start_answer("hi", None)
        await asyncio.wait_for(answer.wait(), 5)
        return answer, await follow(answer)

    with caplog.at_level(logging.ERROR):
        answer, seen = run(go())
    assert answer.events[-1] == PROBLEM and seen[-1] == PROBLEM
    assert op.active("b" * 32) is None
    assert "unexpected" in caplog.text


def test_shutdown_cancels_a_running_answer_and_leaves_no_task(ready):
    async def go():
        op = make_operator(ready, FakeProvider([Turn.hang()]))
        answer = await op.start_answer("hi", None)
        reader = asyncio.create_task(follow(answer))
        await asyncio.sleep(0.05)
        await asyncio.wait_for(op.shutdown(), 5)
        seen = await reader
        leftover = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        return answer, seen, leftover, op

    answer, seen, leftover, op = run(go())
    assert answer.done and seen[-1] == STOPPED and leftover == []
    assert not op._lock.locked()


def test_the_record_is_bounded(monkeypatch):
    monkeypatch.setattr(operator_module, "MAX_EVENTS", 4)
    answer = Answer()
    answer._publish({"type": "conversation", "id": "c" * 32, "title": "t", "finding_id": None})
    for i in range(50):
        answer._publish({"type": "step", "id": f"s{i}", "label": "x", "status": "running"})
        answer._publish({"type": "text", "text": f"{i} "})
    answer._publish({"type": "done", "usage": {}})
    events = answer.events
    assert len(events) <= 4 + 2
    assert events[0]["type"] == "conversation" and events[-1]["type"] == "done"
    # No text is lost: past the limit steps are left out of the record and text is merged.
    assert text_of(events) == "".join(f"{i} " for i in range(50))

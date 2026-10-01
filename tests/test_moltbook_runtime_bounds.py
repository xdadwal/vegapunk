"""Scheduled deadlines, uncapped exploration, and durable operational recovery."""

import asyncio
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests
from logpose import Agent

from tests.fake_provider import FakeProvider, agent_for, says, wants, call
from tests.test_moltbook_tool import _credentials, _FakeResponse
from vegapunk import db, moltbook_actions as ledger, moltbook_backoff, scheduler, task_history
from vegapunk.task_profiles import isolated_agent, close_scheduled_agent
from vegapunk.tools import moltbook


def test_social_profile_has_unlimited_steps_and_respects_stricter_timeouts():
    agent = Agent(FakeProvider(says("done")), max_iterations=100,
                  provider_turn_timeout=600, tool_timeout=600, max_tokens=4000)
    capped = isolated_agent(agent)
    assert (capped.max_iterations, capped.provider_turn_timeout, capped.tool_timeout) == (None, 300, 300)
    assert capped.max_tokens == 2048
    assert agent.max_tokens == 4000
    agent = Agent(FakeProvider(says("done")), max_iterations=3,
                  provider_turn_timeout=4, tool_timeout=5, max_tokens=1000)
    capped = isolated_agent(agent)
    assert (capped.max_iterations, capped.provider_turn_timeout, capped.tool_timeout) == (None, 4, 5)
    assert capped.max_tokens == 1000
    assert isolated_agent(Agent(FakeProvider(says("done")))).max_tokens == 2048
    assert agent.max_iterations == 3  # general template keeps its own cap


def test_requests_are_uncapped_but_expired_scope_refuses_threads():
    from contextvars import copy_context
    scheduler.add_task("explore", 300, profile="moltbook")
    task = scheduler.list_tasks()[0]
    run_id = task_history.begin(task)
    def charge():
        try:
            ledger.charge_request()
            return True
        except ledger.ActionBlocked:
            return False
    with ledger.execution(task.id, run_id):
        contexts = [copy_context() for _ in range(40)]
        late = copy_context()
        with ThreadPoolExecutor(max_workers=8) as pool:
            assert sum(pool.map(lambda ctx: ctx.run(charge), contexts)) == 40
        assert ledger._execution.get().requests == 40
    assert late.run(charge) is False
    ledger.charge_request()  # interactive calls have no scheduled budget


def test_social_run_can_finish_beyond_25_steps_with_a_small_general_cap():
    scheduler.add_task("explore", 300, profile="moltbook")
    scheduler.add_task("ordinary", 300)
    social, general = scheduler.list_tasks()
    script = [wants(call("moltbook_notebook")) for _ in range(30)] + [says("learned")]
    agent, provider = agent_for(script, max_iterations=2)
    try:
        assert scheduler.run_task(social, agent) == "learned"
        assert len(provider.requests) == 31
    finally:
        close_scheduled_agent(agent)
    agent, provider = agent_for(script, max_iterations=2)
    try:
        scheduler.run_task(general, agent)
        assert len(provider.requests) == 2
        assert task_history.list_runs(general.id)[0].status == "error"
    finally:
        close_scheduled_agent(agent)


def test_deadline_revokes_scope_before_context_exit(monkeypatch):
    scheduler.add_task("explore", 300, profile="moltbook")
    task = scheduler.list_tasks()[0]
    run_id = task_history.begin(task)
    clock = [10.0]
    monkeypatch.setattr(ledger.time, "monotonic", lambda: clock[0])
    with ledger.execution(task.id, run_id):
        clock[0] = 1509.0
        ledger.charge_request()  # still valid just before the 25-minute deadline
        clock[0] = 1510.0
        with pytest.raises(ledger.ActionBlocked, match="deadline"):
            ledger.charge_request()


def test_expired_run_cannot_yield_even_an_immediate_provider_event(monkeypatch):
    from vegapunk.task_profiles import scheduled_runtime
    scheduler.add_task("explore", 300, profile="moltbook")
    task = scheduler.list_tasks()[0]
    run_id = task_history.begin(task)
    clock = [10.0]
    monkeypatch.setattr(ledger.time, "monotonic", lambda: clock[0])
    template, provider = agent_for(says("done"))
    with ledger.execution(task.id, run_id), scheduled_runtime(template, isolated_agent(template)) as runtime:
        clock[0] = 1510.0
        with pytest.raises(TimeoutError, match="deadline"):
            asyncio.run(anext(runtime.stream("explore")))
        assert provider.requests == []


def test_whole_run_timeout_on_shared_loop_does_not_drain_sync_tools(tmp_path, monkeypatch):
    from threading import Event
    from vegapunk import task_profiles
    _credentials(tmp_path, monkeypatch)
    monkeypatch.setattr(task_profiles, "MOLTBOOK_RUN_SECONDS", 0.05)
    entered, release, finished = Event(), Event(), Event()
    def hanging(*a, **k):
        entered.set()
        release.wait(3)
        finished.set()
        return _FakeResponse({"success": True})
    monkeypatch.setattr(moltbook, "_get", hanging)
    scheduler.add_task("explore", 300, profile="moltbook")
    task = scheduler.list_tasks()[0]
    agent, _ = agent_for([wants(call("moltbook_home")), says("done")])
    start = time.monotonic()
    try:
        result = scheduler.run_task(task, agent)
        assert entered.is_set()
        assert time.monotonic() - start < 1
        assert not finished.is_set()
        assert "deadline" in result.lower()
        assert task_history.list_runs(task.id)[0].status == "error"
        scheduler.add_task("ordinary", 300)
        assert scheduler.run_task(scheduler.list_tasks()[1], agent) == "done"
    finally:
        release.set()
        close_scheduled_agent(agent)


def test_provider_turns_share_one_run_deadline_and_close_stream(monkeypatch):
    from vegapunk import task_profiles
    monkeypatch.setattr(task_profiles, "MOLTBOOK_RUN_SECONDS", 0.08)
    class SlowProvider(FakeProvider):
        cancelled = False
        async def stream(self, request):
            try:
                async for event in super().stream(request):
                    yield event
            except BaseException:
                self.cancelled = True
                raise
    scheduler.add_task("explore", 300, profile="moltbook")
    provider = SlowProvider([wants(call("moltbook_notebook"), delay=0.05), says("done", delay=0.05)])
    agent = Agent(provider)
    try:
        assert "deadline" in scheduler.run_task(scheduler.list_tasks()[0], agent)
        assert len(provider.requests) == 2
        assert provider.cancelled
    finally:
        close_scheduled_agent(agent)


def test_provider_default_honors_stricter_provider_deadline():
    provider = FakeProvider(says("done"))
    provider.turn_timeout = 7
    assert isolated_agent(Agent(provider)).provider_turn_timeout == 7


@pytest.mark.parametrize("status", [0, 500, 503])
def test_operational_failure_backoff_is_persistent_capped_and_reset(tmp_path, monkeypatch, status):
    _credentials(tmp_path, monkeypatch)
    clock = ["2026-09-30T12:00:00.000000Z"]
    monkeypatch.setattr(db, "utcnow", lambda: clock[0])
    def fail(*a, **k):
        if not status:
            raise requests.ConnectionError("private server text")
        return _FakeResponse({}, status_code=status)
    monkeypatch.setattr(moltbook, "_get", fail)
    for seconds in [60, 120, 240, 480, 960, 1800, 1800]:
        assert moltbook.moltbook_home().startswith("Error:")
        db.close_connection()
        expiry = moltbook_backoff.until("moltbook_test_secret")
        assert expiry == db.stamp_plus(clock[0], seconds)
        clock[0] = expiry
    monkeypatch.setattr(moltbook, "_get", lambda *a, **k: _FakeResponse({"success": True}))
    assert moltbook.moltbook_home().startswith("Untrusted Moltbook data")
    monkeypatch.setattr(moltbook, "_get", fail)
    moltbook.moltbook_home()
    assert moltbook_backoff.until("moltbook_test_secret") == db.stamp_plus(clock[0], 60)


def test_auth_cooldown_is_finite_and_probes_without_human_request(tmp_path, monkeypatch):
    _credentials(tmp_path, monkeypatch)
    clock = ["2026-09-30T12:00:00.000000Z"]
    monkeypatch.setattr(db, "utcnow", lambda: clock[0])
    monkeypatch.setattr(moltbook, "_get", lambda *a, **k: _FakeResponse({}, status_code=401))
    result = moltbook.moltbook_home()
    assert result.startswith("Blocked:") and "human" not in result
    expiry = moltbook_backoff.until("moltbook_test_secret")
    assert expiry == db.stamp_plus(clock[0], 21600)
    clock[0] = expiry
    monkeypatch.setattr(moltbook, "_get", lambda *a, **k: _FakeResponse({"success": True}))
    assert moltbook.moltbook_home().startswith("Untrusted Moltbook data")


def test_success_reset_cannot_shorten_concurrent_cooldown():
    moltbook_backoff.failure("key")
    _, expiry = moltbook_backoff.record("key", "7200")
    moltbook_backoff.success("key")
    assert moltbook_backoff.until("key") == expiry


def test_client_error_prefix_is_operational_error_not_blocked():
    from logpose import ToolResult
    scheduler.add_task("explore", 300, profile="moltbook")
    run_id = task_history.begin(scheduler.list_tasks()[0])
    observer = task_history.RunObserver(run_id)
    observer(ToolResult(id="one", name="moltbook_home", content="Error: network failed", is_error=False))
    assert observer.status() == "error"


def test_generic_plain_error_text_keeps_existing_returned_contract():
    from logpose import ToolResult
    scheduler.add_task("ordinary", 300)
    run_id = task_history.begin(scheduler.list_tasks()[0])
    observer = task_history.RunObserver(run_id)
    observer(ToolResult(id="one", name="generic_tool", content="Error: model-supplied text", is_error=False))
    assert observer.outcomes == ["returned"]


def test_v10_health_upgrade_preserves_existing_cooldown():
    assert db.SCHEMA_VERSION >= 11
    _, expiry = moltbook_backoff.record("key", "7200")
    db.execute("DROP TABLE moltbook_request_health")
    db.execute("UPDATE meta SET value='10' WHERE key='schema_version'")
    db.close_connection()
    assert moltbook_backoff.until("key") == expiry
    assert db.query("SELECT * FROM moltbook_request_health") == []

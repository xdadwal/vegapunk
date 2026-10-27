"""Generic scheduler loop ownership and opt-in run deadlines."""

import asyncio
import threading
import time
from dataclasses import replace

import pytest
from logpose import Agent, tool

from tests.fake_provider import FakeProvider, agent_for, call, says, wants
from vegapunk import loop, scheduler, task_history
from vegapunk.scheduled_runtime import close_scheduled_agent, scheduled_runtime


def test_repeated_runs_share_one_provider_loop_and_close_once():
    class LoopBoundProvider(FakeProvider):
        bound_loop = None
        closes = 0

        async def stream(self, request):
            running = asyncio.get_running_loop()
            if self.bound_loop is None:
                self.bound_loop = running
            assert running is self.bound_loop, "provider crossed event loops"
            async for event in super().stream(request):
                yield event

        async def aclose(self):
            assert asyncio.get_running_loop() is self.bound_loop
            self.closes += 1

    scheduler.add_task("ordinary", 300)
    task = scheduler.list_tasks()[0]
    provider = LoopBoundProvider([says("one"), says("two"), says("three")])
    agent = Agent(provider)
    try:
        for answer in ("one", "two", "three"):
            assert scheduler.run_task(task, agent) == answer
    finally:
        close_scheduled_agent(agent)
        close_scheduled_agent(agent)
    assert provider.closes == 1 and provider.bound_loop.is_closed()


def test_expired_deadline_does_not_call_provider():
    agent, provider = agent_for(says("too late"))
    try:
        with scheduled_runtime(agent, timeout_seconds=0) as runtime:
            with pytest.raises(TimeoutError, match="scheduled run deadline expired"):
                loop.run(runtime, "request")
        assert provider.requests == []
    finally:
        close_scheduled_agent(agent)


def test_whole_run_deadline_is_not_reset_for_each_turn():
    @tool
    def inspect_page() -> str:
        """Return deterministic public evidence."""
        return "page"

    provider = FakeProvider([
        replace(wants(call("inspect_page")), delay=0.06),
        replace(says("too late"), delay=0.12),
    ])
    agent = Agent(provider, tools=[inspect_page], provider_turn_timeout=1)
    try:
        with scheduled_runtime(agent, timeout_seconds=0.15) as runtime:
            with pytest.raises(TimeoutError, match="scheduled run deadline expired"):
                loop.run(runtime, "request")
        assert len(provider.requests) == 2
    finally:
        close_scheduled_agent(agent)


def test_deadline_does_not_drain_sync_tools_and_runtime_can_be_reused():
    started, release, finished = threading.Event(), threading.Event(), threading.Event()

    @tool
    def slow_read() -> str:
        """Simulate an in-flight blocking read without external side effects."""
        started.set()
        try:
            release.wait(timeout=5)
            return "late observation"
        finally:
            finished.set()

    agent, _ = agent_for([wants(call("slow_read")), says("fresh run")], tools=[slow_read])
    begin = time.monotonic()
    try:
        with scheduled_runtime(agent, timeout_seconds=0.15) as runtime:
            with pytest.raises(TimeoutError, match="scheduled run deadline expired"):
                loop.run(runtime, "request")
        assert started.is_set() and not finished.is_set()
        assert time.monotonic() - begin < 2
        with scheduled_runtime(agent) as runtime:
            assert loop.run(runtime, "next request") == "fresh run"
    finally:
        release.set()
        assert finished.wait(timeout=2)
        close_scheduled_agent(agent)


def test_deadline_closes_inflight_provider_stream():
    class HangingProvider(FakeProvider):
        closed = False

        async def stream(self, request):
            try:
                async for event in super().stream(request):
                    yield event
            finally:
                self.closed = True

    provider = HangingProvider(replace(says("too late"), delay=1))
    agent = Agent(provider)
    try:
        with scheduled_runtime(agent, timeout_seconds=0.05) as runtime:
            with pytest.raises(TimeoutError, match="scheduled run deadline expired"):
                loop.run(runtime, "request")
        assert provider.closed
    finally:
        close_scheduled_agent(agent)


@pytest.mark.parametrize("timeout", [-1, float("inf"), float("nan")])
def test_invalid_run_deadline_is_rejected(timeout):
    agent, provider = agent_for(says("unused"))
    with pytest.raises(ValueError, match="finite and non-negative"):
        with scheduled_runtime(agent, timeout_seconds=timeout):
            pytest.fail("invalid deadline accepted")
    assert provider.requests == []


def test_general_agent_keeps_its_step_limit():
    @tool
    def inspect_page() -> str:
        """Return deterministic public evidence."""
        return "page"

    scheduler.add_task("inspect", 300)
    task = scheduler.list_tasks()[0]
    agent, provider = agent_for(wants(call("inspect_page")), tools=[inspect_page],
                                max_iterations=2, repeat_last=True)
    try:
        scheduler.run_task(task, agent)
        assert len(provider.requests) == 2
        assert task_history.list_runs(task.id)[0].status == "error"
    finally:
        close_scheduled_agent(agent)

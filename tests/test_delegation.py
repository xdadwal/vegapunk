"""Role delegation is isolated, concurrent, universal, and joined."""

from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest
from logpose import ToolGateResult, ToolUseBlock, close_sync, run_sync

from tests.fake_provider import FakeProvider, agent_for, backend_for, call, says, session_for, wants
from vegapunk import agents, delegation, prompt
from vegapunk.config import config
from vegapunk.delegation import RoleRunner
from vegapunk.gate import NESTED_DELEGATION, QUESTION_UNAVAILABLE, make_gate
from vegapunk.interactive_agent import InteractiveAgent
from vegapunk.prompter import ScriptedPrompter
from vegapunk.tools import ALL_TOOLS, GUARDED
from vegapunk.tools.delegation import delegate


@pytest.fixture(autouse=True)
def _clear_runner():
    delegation.set_runner(None)
    yield
    delegation.set_runner(None)


def test_role_catalog_separates_responsibility_from_tools():
    catalog = agents.delegation_catalog()

    assert "shaka: Plan, review" in catalog
    assert "lilith: Implement and engineer" in catalog
    assert "atlas: Debug and troubleshoot" in catalog
    assert "default" not in catalog


def test_delegate_tool_is_registered_with_typed_contract():
    registered = next(tool for tool in ALL_TOOLS if tool.name == "delegate")

    assert registered is delegate
    assert registered.input_schema["required"] == ["role", "task"]
    assert registered.input_schema["properties"]["role"]["type"] == "string"
    assert "delegate" not in GUARDED


def test_role_runner_uses_isolated_provider_and_complete_tool_set(monkeypatch, tmp_path):
    primary = backend_for()
    child = FakeProvider()
    primary = replace(primary, spawn_provider=lambda: child)
    captured: dict[str, object] = {}

    class CapturedAgent:
        def __init__(self, provider, **kwargs):
            captured["provider"] = provider
            captured.update(kwargs)

    monkeypatch.setattr(delegation, "_DelegatedAgent", CapturedAgent)
    monkeypatch.setattr(delegation, "run_sync", lambda worker, task: SimpleNamespace(text="Reviewed"))
    monkeypatch.setattr(delegation, "close_sync", lambda worker: captured.setdefault("closed", worker))
    monkeypatch.setattr(prompt.memory, "as_system_block", lambda: "\nPRIVATE MEMORY")
    cfg = replace(config, db_file=tmp_path / "vegapunk.db")

    result = RoleRunner(lambda: primary, ALL_TOOLS, cfg).run(" ShAkA ", "  assess this  ")

    assert result == "Shaka role result:\nReviewed"
    assert captured["provider"] is child
    assert captured["tools"] is ALL_TOOLS
    assert "Agent: Shaka" in captured["system"]
    assert "bounded background specialist" in captured["system"]
    assert "\n\nDelegation:\n" not in captured["system"]
    assert "PRIVATE MEMORY" not in captured["system"]
    assert captured["closed"] is not None


def test_role_runner_closes_spawned_provider_on_its_request_loop(tmp_path):
    class LoopAwareProvider(FakeProvider):
        def __init__(self):
            super().__init__(says("Reviewed"))
            self.request_loop = None
            self.close_loop = None

        async def stream(self, request):
            self.request_loop = asyncio.get_running_loop()
            async for event in super().stream(request):
                yield event

        async def aclose(self):
            self.close_loop = asyncio.get_running_loop()

    primary = backend_for()
    child = LoopAwareProvider()
    primary = replace(primary, spawn_provider=lambda: child)
    cfg = replace(config, db_file=tmp_path / "vegapunk.db")

    result = RoleRunner(lambda: primary, ALL_TOOLS, cfg).run("shaka", "review")

    assert result == "Shaka role result:\nReviewed"
    assert child.close_loop is child.request_loop


@pytest.mark.parametrize("role", ["default", "missing"])
def test_role_runner_rejects_non_delegatable_roles_before_spawning(role):
    spawned = False

    def backend():
        nonlocal spawned
        spawned = True
        return backend_for()

    with pytest.raises(ValueError):
        RoleRunner(backend, ALL_TOOLS).run(role, "task")
    assert spawned is False


def test_role_runner_rejects_empty_task_before_spawning():
    spawned = False

    def backend():
        nonlocal spawned
        spawned = True
        return backend_for()

    with pytest.raises(ValueError, match="must not be empty"):
        RoleRunner(backend, ALL_TOOLS).run("shaka", "   ")
    assert spawned is False


def test_background_gate_blocks_questions_and_recursive_delegation():
    gate = make_gate(None, allow_questions=False, allow_delegation=False)
    question = ToolUseBlock(
        id="q1",
        name="ask_user",
        input={"question": "Choose?", "options": ["One"], "preferred_option": "One"},
    )
    nested = ToolUseBlock(id="d1", name="delegate", input={"role": "shaka", "task": "More"})

    question_result = asyncio.run(gate(question))
    nested_result = asyncio.run(gate(nested))

    assert question_result == ToolGateResult(QUESTION_UNAVAILABLE, is_error=True)
    assert nested_result == ToolGateResult(NESTED_DELEGATION, is_error=True)


def test_parallel_delegate_calls_run_in_background_and_join_before_reply():
    barrier = threading.Barrier(2)
    thread_ids: set[int] = set()

    class ConcurrentRunner:
        def run(self, role: str, task: str) -> str:
            thread_ids.add(threading.get_ident())
            barrier.wait(timeout=2)
            return f"{role} finished {task}"

    delegation.set_runner(ConcurrentRunner())
    agent, provider = agent_for(
        [
            wants(
                call("delegate", {"role": "shaka", "task": "review"}),
                call("delegate", {"role": "atlas", "task": "debug"}),
            ),
            says("synthesized"),
        ],
        tools=[delegate],
    )
    try:
        result = run_sync(agent, "handle this")
    finally:
        close_sync(agent)

    assert result.text == "synthesized"
    assert len(thread_ids) == 2
    assert threading.get_ident() not in thread_ids
    follow_up = repr(provider.requests[1].messages)
    assert "shaka finished review" in follow_up
    assert "atlas finished debug" in follow_up


def test_delegate_ignores_generic_tool_timeout_and_stays_joined():
    class SlowRunner:
        def run(self, role: str, task: str) -> str:
            time.sleep(0.03)
            return "slow result"

    delegation.set_runner(SlowRunner())
    provider = FakeProvider(
        [
            wants(call("delegate", {"role": "shaka", "task": "review"})),
            says("synthesized after waiting"),
        ]
    )
    agent = InteractiveAgent(
        provider,
        system="system",
        tools=[delegate],
        tool_timeout=0.001,
    )
    started = time.monotonic()
    try:
        result = run_sync(agent, "handle this")
    finally:
        close_sync(agent)

    assert time.monotonic() - started >= 0.03
    assert result.text == "synthesized after waiting"
    assert "slow result" in repr(provider.requests[1].messages)
    assert "execution limit" not in repr(provider.requests[1].messages)


def test_cancelled_primary_turn_drains_delegate_before_propagating_cancel():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class BlockingRunner:
        def run(self, role: str, task: str) -> str:
            started.set()
            release.wait(timeout=2)
            finished.set()
            return "finished after cancellation"

    async def scenario() -> None:
        delegation.set_runner(BlockingRunner())
        provider = FakeProvider(
            wants(call("delegate", {"role": "shaka", "task": "review"}))
        )
        agent = InteractiveAgent(provider, system="system", tools=[delegate])
        run = asyncio.create_task(agent.run("handle this"))
        assert await asyncio.to_thread(started.wait, 1)

        run.cancel()
        await asyncio.sleep(0.02)
        assert not run.done()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await run
        assert finished.is_set()

    asyncio.run(scenario())


def test_delegate_without_primary_runner_reports_unavailable():
    assert delegate("shaka", "review") == delegation.UNAVAILABLE


def test_primary_prompt_advertises_optional_joined_delegation():
    primary = prompt.system_prompt(config, mode="manual")
    background = prompt.system_prompt(config, mode="unattended")

    assert "The primary conversation decides" in primary
    assert "same universal tools" in primary
    assert "must synthesize all results before ending your response" in primary
    assert "\n\nDelegation:\n" not in background


def test_cli_installs_and_clears_role_runner(monkeypatch):
    installed: list[object | None] = []
    monkeypatch.setattr("vegapunk.cli.set_runner", installed.append)

    from vegapunk.cli import main

    main(prompter=ScriptedPrompter(["/exit"]), session=session_for([]))

    assert isinstance(installed[0], RoleRunner)
    assert installed[-1] is None

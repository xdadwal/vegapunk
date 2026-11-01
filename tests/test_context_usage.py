"""Usage includes completed model calls once, with missing usage explicit."""

from concurrent.futures import ThreadPoolExecutor
from logpose import TurnEnd, ToolCall, ToolResult, Usage
from vegapunk.usage import UsageLedger


def test_usage_totals_do_not_add_cache_tokens_twice_and_survive_turn_boundary():
    ledger = UsageLedger()
    ledger.observe("main", TurnEnd(stop_reason="tool_use", usage=Usage(input_tokens=100, output_tokens=10, cache_read_input_tokens=80)))
    ledger.observe("main", ToolCall(id="read", name="read_artifact", input={"secret": "never stored"}))
    ledger.observe("main", ToolResult(id="read", name="read_artifact", content="界", is_error=False))
    ledger.observe("summary", TurnEnd(stop_reason="end_turn", usage=Usage(input_tokens=20, output_tokens=5)))
    ledger.observe("delegate", TurnEnd(stop_reason="end_turn", usage=Usage()))
    view = ledger.snapshot()
    assert view["session"]["reported_input_tokens"] == 120
    assert view["session"]["reported_output_tokens"] == 15
    assert view["session"]["cache_read_input_tokens"] == 80
    assert view["session"]["completed_model_calls"] == 3
    assert view["session"]["calls_without_usage"] == 1
    assert view["session"]["retrieval_requests"] == 1
    assert view["session"]["retrieval_result_bytes"] == 3
    assert "never stored" not in str(view)
    ledger.begin_turn()
    assert ledger.snapshot()["turn"]["completed_model_calls"] == 0
    assert ledger.snapshot()["session"]["completed_model_calls"] == 3
    ledger.clear()
    assert ledger.snapshot()["session"]["completed_model_calls"] == 0


def test_concurrent_delegates_are_counted_independently():
    ledger = UsageLedger()
    def record(_):
        ledger.observe("delegate", TurnEnd(stop_reason="end_turn", usage=Usage(input_tokens=7, output_tokens=3)))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(record, range(100)))
    view = ledger.snapshot()
    assert view["session"]["by_actor"]["delegate"]["completed_model_calls"] == 100
    assert view["session"]["reported_input_tokens"] == 700


def test_session_counts_each_completion_once_and_resets_on_restore():
    from tests.fake_provider import session_for, says
    session = session_for([says("one", usage=Usage(input_tokens=9, output_tokens=2)), says("two", usage=Usage(input_tokens=12, output_tokens=3))])
    try:
        list(session.send("first"))
        list(session.send("second"))
        view = session.context_report["usage"]
        assert view["session"]["completed_model_calls"] == 2
        assert view["session"]["reported_input_tokens"] == 21
        assert view["turn"]["reported_input_tokens"] == 12
        session.restore([])
        assert session.context_report["usage"]["session"]["completed_model_calls"] == 0
    finally:
        session.close()


def test_role_runner_contributes_to_shared_conversation_usage():
    from dataclasses import replace
    from tests.fake_provider import FakeProvider, backend_for, says
    from vegapunk.delegation import RoleRunner
    from vegapunk.tools import ALL_TOOLS
    ledger = UsageLedger()
    backend = replace(backend_for(), spawn_provider=lambda: FakeProvider(says("done", usage=Usage(input_tokens=7, output_tokens=3))))
    runner = RoleRunner(lambda: backend, ALL_TOOLS, usage=ledger)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _: runner.run("shaka", "inspect only"), range(2)))
    view = ledger.snapshot()
    assert view["session"]["by_actor"]["delegate"]["completed_model_calls"] == 2
    assert view["session"]["reported_input_tokens"] == 14


def test_rejected_summary_cost_is_counted_before_validation_and_not_cached():
    import asyncio
    import pytest
    from dataclasses import replace
    from logpose import ContextInput, Message, TextBlock
    from logpose.providers.base import ToolSpec
    from tests.fake_provider import FakeProvider, backend_for, says
    from vegapunk import context_summary, session_store
    from vegapunk.config import config
    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    messages = (Message(role="user", content=[TextBlock(text="Keep private")]),
                Message(role="assistant", content=[TextBlock(text="unverified")]),
                Message(role="user", content=[TextBlock(text="Continue")]))
    backend = replace(backend_for(), spawn_provider=lambda: FakeProvider(says('{"notes":[]}', usage=Usage(input_tokens=23, output_tokens=4))))
    request = ContextInput(messages, "system", (ToolSpec("read_artifact", "retrieve", {}),), "fake", 100)
    ledger = UsageLedger()
    with pytest.raises(context_summary.SummaryValidationError):
        asyncio.run(context_summary.summarize(request, messages, sid, replace(config, context_recent_turns=1), lambda: backend, usage=ledger))
    assert ledger.snapshot()["session"]["by_actor"]["summary"]["reported_input_tokens"] == 23


def test_prompt_component_sizes_are_measured_without_storing_source_text(monkeypatch):
    from vegapunk import prompt, context
    from tests.test_context_policy import request
    from vegapunk.config import config
    import asyncio
    monkeypatch.setattr(prompt.memory, "as_system_block", lambda: "\n\nprivate remembered 界")
    monkeypatch.setattr(prompt.skills, "as_system_block", lambda: "\n\nskill catalog")
    sizes = {}
    system = prompt.system_prompt(config, mode="manual", component_sizes=sizes)
    assert sizes == {"memory_bytes": len("\n\nprivate remembered 界".encode()), "skills_bytes": len("\n\nskill catalog".encode())}
    policy = context.ContextManager(lambda: None, components=lambda: sizes)
    asyncio.run(policy(request()))
    assert policy.report["memory_bytes"] == sizes["memory_bytes"]
    assert "private remembered" not in str(policy.report)
    assert "overlap" in policy.report["component_semantics"]
    assert "private remembered" in system


def test_cache_reuse_and_failed_preparation_do_not_fabricate_provider_calls():
    ledger = UsageLedger()
    ledger.preparation("main", {"summary_cache_hit": True, "memory_bytes": 12, "system": "secret text"})
    ledger.preparation("delegate", {"summary_error": "secret text"}, failed=True)
    view = ledger.snapshot()
    assert view["session"]["summary_cache_hits"] == 1
    assert view["session"]["completed_model_calls"] == 0
    assert view["session"]["reported_input_tokens"] is None
    assert view["session"]["failed_preparations"] == 1
    assert view["latest_preparation_by_actor"]["main"]["memory_bytes"] == 12
    assert "secret text" not in str(view)


def test_failed_followup_keeps_prior_usage_without_inventing_current_usage():
    import pytest
    from tests.fake_provider import session_for, says, Turn
    session = session_for([says("done", usage=Usage(input_tokens=9, output_tokens=2)), Turn(error=ValueError("synthetic failure"))])
    try:
        list(session.send("first"))
        with pytest.raises(ValueError, match="synthetic failure"):
            list(session.send("second"))
        view = session.context_report["usage"]
        assert view["session"]["completed_model_calls"] == 1
        assert view["turn"]["reported_input_tokens"] is None
        assert "unreported cost" in view["coverage"]
    finally:
        session.close()


def test_hidden_interview_retry_usage_is_counted_once():
    from logpose import close_sync, run_sync
    from tests.fake_provider import FakeProvider, wants, call, says
    from vegapunk.interactive_agent import InteractiveAgent
    from vegapunk.tools.questions import ask_user
    from vegapunk.gate import make_gate
    ledger = UsageLedger()
    provider = FakeProvider([
        wants(call("ask_user", {"question": "Choose", "options": ["Wait"], "preferred_option": "Wait"}), usage=Usage(input_tokens=10, output_tokens=2)),
        says("Which option do you want?", usage=Usage(input_tokens=20, output_tokens=3)),
        says("FINAL: I will wait.", usage=Usage(input_tokens=30, output_tokens=4)),
    ])
    agent = InteractiveAgent(provider, tools=[ask_user], on_tool_call=make_gate(None), usage=ledger)
    try:
        run_sync(agent, "Choose")
        view = ledger.snapshot()["session"]
        assert view["completed_model_calls"] == 3
        assert view["reported_input_tokens"] == 60
    finally:
        close_sync(agent)


def test_cancelled_summary_closes_owned_provider_without_inventing_usage():
    import asyncio
    import pytest
    from dataclasses import replace
    from logpose import ContextInput, Message, TextBlock
    from logpose.providers.base import ToolSpec
    from tests.fake_provider import FakeProvider, backend_for
    from vegapunk import context_summary, session_store
    from vegapunk.config import config
    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    ledger = UsageLedger()
    messages = (Message(role="user", content=[TextBlock(text="Keep private")]),
                Message(role="assistant", content=[TextBlock(text="unverified")]),
                Message(role="user", content=[TextBlock(text="Continue")]))
    request = ContextInput(messages, "system", (ToolSpec("read_artifact", "retrieve", {}),), "fake", 100)
    async def run():
        started = asyncio.Event()
        class PendingProvider(FakeProvider):
            stream_closed = False
            provider_closed = False
            async def stream(self, request):
                started.set()
                try:
                    await asyncio.Event().wait()
                    yield None
                finally:
                    self.stream_closed = True
            async def aclose(self):
                self.provider_closed = True
        child = PendingProvider()
        backend = replace(backend_for(), spawn_provider=lambda: child)
        task = asyncio.create_task(context_summary.summarize(request, messages, sid, replace(config, context_recent_turns=1), lambda: backend, usage=ledger))
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert child.stream_closed and child.provider_closed
    asyncio.run(run())
    assert ledger.snapshot()["session"]["completed_model_calls"] == 0
    assert ledger.snapshot()["session"]["reported_input_tokens"] is None

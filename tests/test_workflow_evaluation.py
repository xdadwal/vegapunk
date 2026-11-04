"""Operational evaluation counts summaries and real joined delegates together."""

import asyncio
import json
from dataclasses import replace
from logpose import Usage
from tests.fake_provider import FakeProvider, backend_for, wants, says, call
from vegapunk import db
from vegapunk.evaluate_workflow import run_workflow


def test_bounded_workflow_exercises_summary_delegation_and_observed_evidence():
    class Script(FakeProvider):
        def __init__(self, child=False):
            super().__init__()
            self.child = child
        async def stream(self, request):
            artifact_id = db.query("SELECT artifact_id FROM session_artifacts WHERE title='Workflow evidence'")[0][0]
            if self.child:
                self.turns = [wants(call("read_artifact", {"artifact_id": artifact_id}), usage=Usage(input_tokens=20, output_tokens=5)),
                              says(f"Observed unknown outcome. Inspect before retrying. Source: {artifact_id}", usage=Usage(input_tokens=30, output_tokens=5))]
            else:
                self.turns = [wants(call("delegate", {"role": "shaka", "task": f"Independently read artifact {artifact_id} and report whether retry is safe."}), usage=Usage(input_tokens=100, output_tokens=5)),
                              says(json.dumps({"answer": "inspect-before-retry", "source": artifact_id}), usage=Usage(input_tokens=100, output_tokens=5))]
            async for event in super().stream(request):
                yield event
    summary = FakeProvider(says(json.dumps({"notes": [{"text": "Outcome unknown", "source": "2:0", "quote": "OUTCOME: unknown", "kind": "observation"}]}), usage=Usage(input_tokens=50, output_tokens=10)))
    clients = [summary, Script(child=True)]
    backend = replace(backend_for(), provider=Script(), spawn_provider=lambda: clients.pop(0))
    report = asyncio.run(run_workflow(backend, "bounded"))
    assert report["correct"] and report["forbidden_handler_calls"] == 0
    assert report["delegated_work_completed"]
    assert report["usage"]["by_actor"]["summary"]["completed_model_calls"] == 1
    assert report["usage"]["by_actor"]["delegate"]["completed_model_calls"] == 2
    assert report["usage"]["reported_input_tokens"] == 300
    assert report["canonical_history_preserved"]


def test_failed_workflow_keeps_reported_summary_cost():
    summary = FakeProvider(says('{"notes":[]}', usage=Usage(input_tokens=50, output_tokens=10)))
    backend = replace(backend_for(), spawn_provider=lambda: summary)
    report = asyncio.run(run_workflow(backend, "bounded"))
    assert not report["correct"]
    assert report["usage"]["reported_input_tokens"] == 50
    assert report["context"]["summary_error"] == "SummaryValidationError"
    assert report["rejected_synthetic_summary"] == '{"notes":[]}'
    assert not report["rejected_summary_truncated"]


def test_paired_workflow_setup_uses_distinct_historical_runtime_identity():
    async def paired():
        baseline = await run_workflow(backend_for(says("{}", usage=Usage(input_tokens=1))), "baseline")
        summary = FakeProvider(says(json.dumps({"notes": [{"text": "Unknown", "source": "2:0", "quote": "OUTCOME: unknown", "kind": "observation"}]})))
        bounded = await run_workflow(replace(backend_for(says("{}", usage=Usage(input_tokens=1))), spawn_provider=lambda: summary), "bounded")
        return baseline, bounded
    baseline, bounded = asyncio.run(paired())
    assert baseline["mode"] == "baseline" and bounded["mode"] == "bounded"
    assert len(db.query("SELECT DISTINCT runtime_run_id FROM tool_observations WHERE tool_call_id='old-inspect'")) == 2


def test_parent_and_child_binding_cannot_restore_host_file_registration(monkeypatch):
    from vegapunk import artifacts
    from vegapunk.evaluate_workflow import _workflow_tools
    from vegapunk.tools.artifacts import bind
    def forbidden(*args, **kwargs):
        raise AssertionError("Host file handler reached")
    monkeypatch.setattr(artifacts, "register_file", forbidden)
    tools = _workflow_tools(lambda **kwargs: "inert")
    assert "register_file_artifact" not in {t.name for t in tools}
    for _ in range(2):
        tools = bind(tools, lambda: "isolated-session")
        inert = next(t for t in tools if t.name == "unavailable_register_file_artifact")
        assert inert(title="not read", path="not-read.txt") == "inert"


def test_setup_failure_still_closes_provider(monkeypatch):
    import vegapunk.evaluate_workflow as workflow
    class Closable(FakeProvider):
        closed = False
        async def aclose(self):
            self.closed = True
    provider = Closable()
    def broken(*args, **kwargs):
        raise OSError("synthetic setup failure")
    monkeypatch.setattr(workflow.session_store, "save_session", broken)
    report = asyncio.run(run_workflow(replace(backend_for(), provider=provider), "bounded"))
    assert not report["correct"] and provider.closed
    assert report["usage"]["completed_model_calls"] == 0


def test_cleanup_failure_preserves_completed_usage():
    class CloseFailure(FakeProvider):
        async def aclose(self):
            raise OSError("synthetic close failure")
    provider = CloseFailure(says("{}", usage=Usage(input_tokens=7)))
    report = asyncio.run(run_workflow(replace(backend_for(), provider=provider), "baseline"))
    assert report["usage"]["reported_input_tokens"] == 7
    assert report["cleanup_error"]["type"] == "OSError"
    assert not report["correct"]

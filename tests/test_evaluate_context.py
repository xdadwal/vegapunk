"""Evaluation must distinguish evidence accuracy from response formatting."""

from vegapunk.evaluate_context import grade_response


def test_grading_requires_exact_answer_and_resolvable_source():
    direct = grade_response('{"answer":"failed","source":"abc"}', "failed", "abc")
    assert all(direct.values())
    fenced = grade_response('```json\n{"answer":"failed","source":"abc"}\n```', "failed", "abc")
    assert fenced["correct_answer_and_source"] and not fenced["requested_json_format"]
    assert not grade_response('{"answer":"success","source":"abc"}', "failed", "abc")["correct_answer_and_source"]
    assert not grade_response('{"answer":"failed","source":"invented"}', "failed", "abc")["correct_answer_and_source"]
    assert not grade_response('[]', "failed", "abc")["correct_answer_and_source"]


def test_unavailable_source_must_be_explicit_null_not_missing():
    assert grade_response('{"answer":"unavailable","source":null}', "unavailable", None)["correct_answer_and_source"]
    assert not grade_response('{"answer":"unavailable"}', "unavailable", None)["correct_answer_and_source"]


def test_deterministic_suite_covers_distinct_source_and_recovery_scenarios():
    import asyncio
    from vegapunk.evaluate_context import _evaluate
    report = asyncio.run(_evaluate(False, None))
    assert len({case["scenario"] for case in report["cases"]}) == 10
    assert all(case["projection_preserved_user_messages"] for case in report["cases"])
    assert report["cases"][7]["expected_source"] is None
    assert report["cases"][8]["expected_answer"] == "1"
    assert report["default_policy_changed"] is False


def test_pair_assessment_cannot_pass_with_missing_usage_or_correctness_regression():
    from copy import deepcopy
    from vegapunk.evaluate_context import assess_pairs
    cases = []
    for _ in range(10):
        baseline = {"correct_answer_and_source": True, "answer_matches_expected": True, "source_matches_expected": True, "citation_resolves": True,
                    "requested_json_format": True, "reported_input_tokens": 100, "total_input_tokens": 100,
                    "reported_output_tokens": 5, "retrieval_calls": 0, "seconds": 1,
                    "usage_available": True, "foreign_data_leaked": False}
        cases.append({"live": {"baseline": baseline, "bounded": {**baseline, "reported_input_tokens": 50, "total_input_tokens": 50}}})
    assert assess_pairs(cases)["source_accuracy_gate"] == "passed"
    assert assess_pairs(cases[:1])["source_accuracy_gate"] == "unverified"
    missing = deepcopy(cases)
    missing[0]["live"]["bounded"]["usage_available"] = False
    assert assess_pairs(missing)["source_accuracy_gate"] == "failed"
    cases[0]["live"]["bounded"]["correct_answer_and_source"] = False
    assert assess_pairs(cases)["source_accuracy_gate"] == "failed"
    assert not assess_pairs(cases)["no_hard_correctness_regression"]


def test_failed_answer_cannot_hide_a_citation_regression():
    from vegapunk.evaluate_context import assess_pairs
    baseline = {"correct_answer_and_source": False, "answer_matches_expected": False, "source_matches_expected": True, "citation_resolves": True,
                "requested_json_format": True, "reported_input_tokens": 100, "total_input_tokens": 100, "reported_output_tokens": 1,
                "retrieval_calls": 0, "seconds": 1, "usage_available": True, "foreign_data_leaked": False}
    cases = [{"live": {"baseline": dict(baseline), "bounded": {**baseline, "source_matches_expected": False}}} for _ in range(10)]
    assert not assess_pairs(cases)["no_hard_correctness_regression"]


def test_citation_validity_is_distinct_from_matching_the_expected_source():
    result = grade_response('{"answer":"old","source":"old-snapshot"}', "new", "latest-user", known_sources={"old-snapshot", "latest-user"})
    assert not result["source_matches_expected"] and result["citation_resolves"]
    invented = grade_response('{"answer":"old","source":"invented-label"}', "new", "latest-user", known_sources={"old-snapshot", "latest-user"})
    assert not invented["citation_resolves"]


def test_gate_does_not_mistake_cache_warmth_for_smaller_total_input():
    from vegapunk.evaluate_context import assess_pairs
    good = {"correct_answer_and_source": True, "answer_matches_expected": True, "source_matches_expected": True,
            "citation_resolves": True, "requested_json_format": True, "reported_input_tokens": 100,
            "total_input_tokens": 1000, "reported_output_tokens": 1, "retrieval_calls": 0,
            "seconds": 1, "usage_available": True, "foreign_data_leaked": False}
    cases = [{"live": {"baseline": dict(good), "bounded": {**good, "reported_input_tokens": 10, "total_input_tokens": 1200}}} for _ in range(10)]
    assert assess_pairs(cases)["source_accuracy_gate"] == "failed"


def test_source_cleanup_error_keeps_completed_mode_usage(monkeypatch):
    import asyncio
    import json
    from dataclasses import replace
    from logpose import Usage
    from tests.fake_provider import FakeProvider, backend_for, says
    from vegapunk import backend, db
    from vegapunk.evaluate_context import _evaluate
    class CloseFailure(FakeProvider):
        async def stream(self, request):
            source = db.query("SELECT artifact_id FROM session_artifacts WHERE title='Synthetic evidence'")[0][0]
            self.turns = [says(json.dumps({"answer": "service-0-private", "source": source}), usage=Usage(input_tokens=7, output_tokens=1))]
            async for event in super().stream(request):
                yield event
        async def aclose(self):
            raise OSError("synthetic cleanup failure")
    monkeypatch.setattr(backend, "create_backend", lambda *args, **kwargs: replace(backend_for(), spawn_provider=CloseFailure))
    report = asyncio.run(_evaluate(True, "codex", 0))
    assert report["cases"][0]["live"]["baseline"]["total_input_tokens"] == 7
    assert report["cleanup_error"]["type"] == "OSError"
    assert report["assessment"]["source_accuracy_gate"] == "failed"


def test_source_failure_keeps_partial_reported_usage(monkeypatch):
    import asyncio
    from dataclasses import replace
    from logpose import Usage
    from tests.fake_provider import FakeProvider, backend_for, wants, call, Turn
    from vegapunk import backend
    from vegapunk.evaluate_context import _evaluate
    monkeypatch.setattr(backend, "create_backend", lambda *args, **kwargs: replace(backend_for(), spawn_provider=lambda: FakeProvider([
        wants(call("get_plan"), usage=Usage(input_tokens=7, output_tokens=1)), Turn(error=ValueError("synthetic failure"))])))
    report = asyncio.run(_evaluate(True, "codex", 0))
    assert report["partial_mode_usage"]["usage"]["total_input_tokens"] == 7
    assert report["assessment"]["source_accuracy_gate"] == "unverified"


def test_mixed_iteration_usage_cannot_hide_a_missing_completion():
    import asyncio
    from logpose import Agent, Usage, tool, Conversation, Message, TextBlock
    from tests.fake_provider import FakeProvider, wants, says, call
    from vegapunk.evaluate_context import _run_with_usage
    from vegapunk.usage import UsageLedger
    @tool
    def inspect() -> str:
        """Return synthetic evidence."""
        return "synthetic"
    provider = FakeProvider([wants(call("inspect")), says("done", usage=Usage(input_tokens=10, output_tokens=2))])
    ledger = UsageLedger()
    result = asyncio.run(_run_with_usage(Agent(provider, tools=[inspect]), Conversation([Message(role="user", content=[TextBlock(text="Inspect")])]), ledger))
    assert result.usage.input_tokens == 10
    assert ledger.snapshot()["session"]["calls_without_usage"] == 1
    assert ledger.snapshot()["session"]["completed_model_calls"] == 2

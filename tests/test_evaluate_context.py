"""Evaluation must distinguish evidence accuracy from response formatting."""

from vegapunk.evaluate_context import grade_response


def test_grading_requires_exact_answer_and_resolvable_source():
    assert grade_response('{"answer":"failed","source":"abc"}', "failed", "abc") == {
        "correct_answer_and_source": True, "requested_json_format": True}
    assert grade_response('```json\n{"answer":"failed","source":"abc"}\n```', "failed", "abc") == {
        "correct_answer_and_source": True, "requested_json_format": False}
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
        baseline = {"correct_answer_and_source": True, "requested_json_format": True, "reported_input_tokens": 100,
                    "reported_output_tokens": 5, "retrieval_calls": 0, "seconds": 1,
                    "usage_available": True, "foreign_data_leaked": False}
        cases.append({"live": {"baseline": baseline, "bounded": {**baseline, "reported_input_tokens": 50}}})
    assert assess_pairs(cases)["source_accuracy_gate"] == "passed"
    assert assess_pairs(cases[:1])["source_accuracy_gate"] == "unverified"
    missing = deepcopy(cases)
    missing[0]["live"]["bounded"]["usage_available"] = False
    assert assess_pairs(missing)["source_accuracy_gate"] == "failed"
    cases[0]["live"]["bounded"]["correct_answer_and_source"] = False
    assert assess_pairs(cases)["source_accuracy_gate"] == "failed"
    assert not assess_pairs(cases)["no_hard_correctness_regression"]


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

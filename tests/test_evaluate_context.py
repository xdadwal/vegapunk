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

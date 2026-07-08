"""Tests for ClaudeBrain's streaming fence filter — pure, deterministic.

``_FenceFilter`` guards two invariants at once: the user never sees a
``vega_tool`` fence stream by as raw JSON, and nothing the model said is ever
silently dropped (a malformed fence comes back out as visible text). The
matrix here drives the filter delta-by-delta, exactly as ``think`` will.
"""

from __future__ import annotations

from vegapunk.claude_brain import _FenceFilter

FENCE = '```vega_tool\n{"name": "clock", "arguments": {}}\n```\n'


def _run(deltas: list[str]) -> tuple[str, list[dict]]:
    """Feed deltas through a filter; return (all visible text, parsed calls)."""
    fence_filter = _FenceFilter()
    visible = "".join(fence_filter.feed(delta) for delta in deltas)
    visible += fence_filter.finish()
    return visible, fence_filter.calls


def test_plain_text_passes_through_byte_identical():
    text = "The answer is 42.\nNothing else to it."
    visible, calls = _run([text[:7], text[7:]])
    assert visible == text
    assert calls == []


def test_fence_at_reply_start_is_suppressed_and_parsed():
    visible, calls = _run([FENCE])
    assert visible == ""
    assert calls == [{"name": "clock", "arguments": {}}]


def test_fence_after_text_keeps_the_text_and_parses_the_call():
    visible, calls = _run(["Let me check.\n", FENCE])
    assert visible == "Let me check.\n"
    assert calls == [{"name": "clock", "arguments": {}}]


def test_text_after_a_fence_still_streams():
    visible, calls = _run([FENCE, "Done looking."])
    assert visible == "Done looking."
    assert len(calls) == 1


def test_fence_split_across_many_deltas_still_parses():
    # Character-by-character — the harshest split a stream can produce.
    visible, calls = _run(list(FENCE))
    assert visible == ""
    assert calls == [{"name": "clock", "arguments": {}}]


def test_ordinary_code_fences_stream_through_untouched():
    reply = "Use this:\n```python\nprint('hi')\n```\nThat's it."
    visible, calls = _run([reply[:12], reply[12:20], reply[20:]])
    assert visible == reply
    assert calls == []


def test_lone_backtick_tease_at_end_of_stream_is_flushed():
    visible, calls = _run(["tick ", "``"])
    assert visible == "tick ``"
    assert calls == []


def test_malformed_json_body_is_reemitted_verbatim_not_swallowed():
    bad = '```vega_tool\n{"name": "clock", oops}\n```\n'
    visible, calls = _run(["before\n", bad])
    assert visible == "before\n" + bad
    assert calls == []


def test_body_without_a_name_is_treated_as_malformed():
    bad = '```vega_tool\n{"arguments": {}}\n```\n'
    visible, calls = _run([bad])
    assert visible == bad
    assert calls == []


def test_non_dict_arguments_is_treated_as_malformed():
    bad = '```vega_tool\n{"name": "clock", "arguments": [1]}\n```\n'
    visible, calls = _run([bad])
    assert visible == bad
    assert calls == []


def test_missing_arguments_defaults_to_empty_dict():
    visible, calls = _run(['```vega_tool\n{"name": "clock"}\n```\n'])
    assert visible == ""
    assert calls == [{"name": "clock", "arguments": {}}]


def test_unterminated_fence_at_end_of_stream_is_flushed_as_text():
    dangling = '```vega_tool\n{"name": "clock"'
    visible, calls = _run(["answer\n", dangling])
    assert visible == "answer\n" + dangling
    assert calls == []


def test_closer_without_trailing_newline_still_closes_the_fence():
    visible, calls = _run(['```vega_tool\n{"name": "clock"}\n```'])
    assert visible == ""
    assert calls == [{"name": "clock", "arguments": {}}]


def test_two_fences_yield_two_calls_in_order():
    first = '```vega_tool\n{"name": "clock", "arguments": {}}\n```\n'
    second = '```vega_tool\n{"name": "battery", "arguments": {"unit": "pct"}}\n```\n'
    visible, calls = _run([first, "and\n", second])
    assert visible == "and\n"
    assert [call["name"] for call in calls] == ["clock", "battery"]
    assert calls[1]["arguments"] == {"unit": "pct"}


def test_multiline_arguments_json_parses():
    fence = '```vega_tool\n{\n  "name": "write_file",\n  "arguments": {\n    "path": "a.md"\n  }\n}\n```\n'
    visible, calls = _run([fence])
    assert visible == ""
    assert calls == [{"name": "write_file", "arguments": {"path": "a.md"}}]


def test_vega_fence_quoted_inside_a_code_block_is_displayed_not_executed():
    # The model showing its own tool syntax as an example — the example must
    # reach the screen and must NOT become a real tool call.
    reply = 'Example:\n```\n```vega_tool\n{"name": "read_file", "arguments": {"path": "x"}}\n```\n'
    visible, calls = _run([reply[:20], reply[20:]])
    assert visible == reply
    assert calls == []


def test_vega_fence_quoted_inside_a_tagged_code_block_is_displayed_not_executed():
    reply = "Like this:\n```markdown\n" + FENCE + "```\nDone.\n"
    visible, calls = _run([reply])
    assert visible == reply
    assert calls == []


def test_real_call_after_a_closed_code_block_still_parses():
    reply = "Check:\n```python\nprint('hi')\n```\n" + FENCE
    visible, calls = _run([reply[:15], reply[15:30], reply[30:]])
    assert visible == "Check:\n```python\nprint('hi')\n```\n"
    assert calls == [{"name": "clock", "arguments": {}}]

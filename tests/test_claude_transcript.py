"""Tests for ClaudeBrain's transcript rendering — deterministic, no SDK/network.

Every ``think`` call is stateless: the whole OpenAI-shaped history is rendered
into one labeled transcript. These pin that rendering — the system split, turn
ordering, and the verbatim replay of tool calls and results — plus the
tool-protocol stanza built from OpenAI function schemas.
"""

from __future__ import annotations

import json

from vegapunk.claude_brain import _render_transcript, _tool_instructions


def test_system_message_is_split_off_from_the_transcript():
    system, prompt = _render_transcript(
        [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "hello"},
        ]
    )
    assert system == "SYS"
    assert prompt == "[user]\nhello"


def test_history_without_a_system_message_renders_entirely_as_transcript():
    system, prompt = _render_transcript([{"role": "user", "content": "hello"}])
    assert system == ""
    assert prompt == "[user]\nhello"


def test_turns_render_in_order_with_role_labels():
    _, prompt = _render_transcript(
        [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "follow-up"},
        ]
    )
    assert prompt == "[user]\nquestion\n\n[assistant]\nanswer\n\n[user]\nfollow-up"


def test_assistant_tool_calls_render_name_id_and_verbatim_arguments():
    _, prompt = _render_transcript(
        [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "a.md"}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_abc", "content": "file contents"},
        ]
    )
    assert prompt == (
        '[assistant called tool read_file (call id: call_abc) with arguments:]\n{"path": "a.md"}'
        "\n\n[tool result for call call_abc]\nfile contents"
    )


def test_assistant_with_both_text_and_tool_calls_renders_both():
    _, prompt = _render_transcript(
        [
            {
                "role": "assistant",
                "content": "let me check",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "clock", "arguments": "{}"},
                    }
                ],
            }
        ]
    )
    assert prompt == (
        "[assistant]\nlet me check\n\n"
        "[assistant called tool clock (call id: c1) with arguments:]\n{}"
    )


def _schema(name: str, description: str, parameters: dict) -> dict:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": parameters},
    }


def test_tool_instructions_cover_every_tool_and_the_fence_protocol():
    params = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    text = _tool_instructions(
        [
            _schema("read_file", "Read a file from the workspace.", params),
            _schema("clock", "Tell the current time.", {"type": "object", "properties": {}}),
        ]
    )
    assert "### read_file" in text
    assert "Read a file from the workspace." in text
    assert json.dumps(params) in text
    assert "### clock" in text
    assert "```vega_tool" in text  # the protocol example Claude is told to copy
    assert '{"name": "<tool name>"' in text

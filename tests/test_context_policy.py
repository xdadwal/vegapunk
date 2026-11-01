"""Observation and artifact projection preserve canonical conversation data."""

import asyncio
from dataclasses import replace

from logpose import ContextInput, Message, TextBlock, ToolUseBlock, ToolResultBlock
from logpose.providers.base import ToolSpec

from vegapunk import artifacts, context, db, session_store
from vegapunk.config import config


def request():
    return ContextInput((Message(role="user", content=[TextBlock(text="Keep the service private")]),
                         Message(role="assistant", content=[ToolUseBlock(id="old", name="inspect", input={})]),
                         Message(role="user", content=[ToolResultBlock(tool_use_id="old", content="x" * 12000)]),
                         Message(role="user", content=[TextBlock(text="Continue")]),),
                        "instructions", (ToolSpec("read_artifact", "retrieve", {}),), "fake", 1000)


def setup_artifact():
    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    saved = artifacts.store_text(sid, "evidence", "x" * 12000)
    db.execute("INSERT INTO tool_observations VALUES ('obs',?,'runtime','old','inspect','{}','completed',?,'now','now')", (sid, saved["id"]))
    return sid, saved


def test_observe_returns_identical_input_and_reports_proposed_reduction():
    sid, _ = setup_artifact()
    original = request()
    policy = context.ContextManager(lambda: sid, replace(config, context_recent_turns=1))
    prepared = asyncio.run(policy(original))
    assert prepared.messages == original.messages
    assert prepared.system == original.system
    assert prepared.diagnostics["artifact_replacements"] == 1
    assert prepared.diagnostics["proposed_estimated_tokens"] < prepared.diagnostics["retained_estimated_tokens"]


def test_bounded_projects_only_tool_content_and_preserves_user_constraints():
    sid, saved = setup_artifact()
    original = request()
    policy = context.ContextManager(lambda: sid, replace(config, context_policy="bounded", context_recent_turns=1))
    prepared = asyncio.run(policy(original))
    assert saved["id"] in prepared.messages[2].content[0].content
    assert prepared.messages[0] == original.messages[0]
    assert prepared.messages[1] == original.messages[1]
    assert original.messages[2].content[0].content == "x" * 12000


def test_signed_exchange_and_ambiguous_tool_ids_are_not_projected():
    from logpose import ThinkingBlock

    sid, saved = setup_artifact()
    original = request()
    original.messages[1].content.insert(0, ThinkingBlock(thinking="reason", signature="signed"))
    policy = context.ContextManager(lambda: sid, replace(config, context_policy="bounded", context_recent_turns=1))
    result = asyncio.run(policy(original))
    assert result.messages[2] == original.messages[2]
    original.messages[1].content.pop(0)
    db.execute("INSERT INTO tool_observations VALUES ('duplicate',?,'another','old','inspect','{}','completed',?,'now','now')", (sid, saved["id"]))
    result = asyncio.run(policy(original))
    assert result.messages[2] == original.messages[2]


def test_budget_error_does_not_silently_drop_user_history():
    import pytest

    sid, _ = setup_artifact()
    policy = context.ContextManager(lambda: sid, replace(config, context_policy="bounded", context_input_budget=1))
    with pytest.raises(context.ContextBudgetError):
        asyncio.run(policy(request()))
    assert policy.report["estimated_budget_exceeded"]


def test_unsigned_reasoning_keeps_original_blocks_while_projecting_results():
    from logpose import ThinkingBlock

    sid, _ = setup_artifact()
    original = request()
    thinking = ThinkingBlock(thinking="reason", signature="")
    original.messages[1].content.insert(0, thinking)
    result = asyncio.run(context.ContextManager(lambda: sid, replace(config, context_policy="bounded", context_recent_turns=1))(original))
    assert result.messages[1].content[0] == thinking
    assert len(result.messages[2].content[0].content) < 12000


def test_incomplete_artifact_never_replaces_retained_result():
    sid, saved = setup_artifact()
    db.execute("UPDATE session_artifacts SET complete=0 WHERE artifact_id=?", (saved["id"],))
    original = request()
    result = asyncio.run(context.ContextManager(lambda: sid, replace(config, context_policy="bounded", context_recent_turns=1))(original))
    assert result.messages[2] == original.messages[2]


def test_reused_historical_id_with_only_one_observation_is_not_attributed():
    sid, _ = setup_artifact()
    original = request()
    first_result = Message(role="user", content=[ToolResultBlock(tool_use_id="old", content="OLD" + "x" * 12000)])
    messages = (original.messages[0], original.messages[1], first_result,
                Message(role="user", content=[TextBlock(text="Another task")]),
                original.messages[1].model_copy(deep=True), original.messages[2], original.messages[3])
    result = asyncio.run(context.ContextManager(lambda: sid, replace(config, context_policy="bounded", context_recent_turns=1))(replace(original, messages=messages)))
    assert result.messages[2] == first_result
    assert result.messages[5] == original.messages[2]

"""Long retained history can retrieve offloaded evidence without replaying actions."""

import asyncio
from dataclasses import replace
from logpose import Agent, Conversation, Message, TextBlock, ToolUseBlock, ToolResultBlock
from tests.fake_provider import FakeProvider, call, says, wants
from vegapunk import artifacts, context, db, session_store
from vegapunk.config import config
from vegapunk.tools import ALL_TOOLS
from vegapunk.tools.artifacts import bind


def test_forty_turn_megabyte_result_retrieval_keeps_corrections_and_canonical_history():
    session_store.save_session("one", [])
    session_store.save_session("other", [])
    sid = session_store.session_identity("one")
    body = "initial evidence\n" + "x" * (1024 * 1024) + "\nANSWER: inspect-only\n" + "z" * 2000
    saved = artifacts.store_text(sid, "large source", body)
    foreign = artifacts.store_text(session_store.session_identity("other"), "private", "foreign evidence")
    db.execute("INSERT INTO tool_observations VALUES ('obs',?,'runtime','old','inspect','{}','completed',?,'now','now')", (sid, saved["id"]))
    messages = [Message(role="user", content=[TextBlock(text="Keep services private")]),
                Message(role="assistant", content=[ToolUseBlock(id="old", name="inspect", input={})]),
                Message(role="user", content=[ToolResultBlock(tool_use_id="old", content=body)])]
    for index in range(1, 39):
        messages.extend([Message(role="user", content=[TextBlock(text=f"Requirement {index}: inspect only")]),
                         Message(role="assistant", content=[TextBlock(text="Acknowledged")])])
    messages.append(Message(role="user", content=[TextBlock(text="Correction: never restart anything. Find the ANSWER evidence.")]))
    retained = [m.model_dump(mode="json") for m in messages]
    provider = FakeProvider([wants(call("search_artifact", {"artifact_id": saved["id"], "query": "ANSWER:"})),
                             wants(call("read_artifact", {"artifact_id": foreign["id"]})), says("Evidence retrieved; foreign evidence unavailable.")])
    tools = bind([t for t in ALL_TOOLS if t.name in {"read_artifact", "search_artifact"}], lambda: sid)
    policy = context.ContextManager(lambda: sid, replace(config, context_policy="bounded", context_recent_turns=4))
    conversation = Conversation(messages)
    result = asyncio.run(Agent(provider, tools=tools, context_policy=policy).run(conversation=conversation))
    assert [m.model_dump(mode="json") for m in conversation.messages[:len(messages)]] == retained
    for request in provider.requests:
        users = [m for m in request.messages if m.role == "user" and not any(b.type == "tool_result" for b in m.content)]
        assert len(users) == 40 and "never restart" in users[-1].text
        assert len(request.messages[2].content[0].content) < 2500
    blocks = [b for m in result.messages for b in m.content if b.type == "tool_result"]
    results = [b.content for b in blocks]
    assert any("ANSWER: inspect-only" in text and len(text) < 1000 for text in results)
    assert blocks[-1].is_error
    assert all("foreign evidence" not in text for text in results)
    assert len(provider.requests) == 3

"""Summary notes must carry quotes resolvable to retained source records."""

import json
import pytest
from vegapunk import context_summary


def test_summary_rejects_fabricated_quote_or_source():
    sources = [{"id": "0:0", "text": "observed failure", "kind": "tool_result"}]
    valid = {"notes": [{"text": "The check failed", "source": "0:0", "quote": "observed failure", "kind": "observation"}]}
    assert context_summary.parse(json.dumps(valid), sources)
    valid["notes"][0]["quote"] = "passed"
    with pytest.raises(ValueError, match="quote"):
        context_summary.parse(json.dumps(valid), sources)


def test_source_linked_summary_is_cached_without_dropping_user_messages():
    import asyncio
    from dataclasses import replace
    from tests.fake_provider import backend_for, says, FakeProvider
    from logpose import ContextInput, Message, TextBlock
    from logpose.providers.base import ToolSpec
    from vegapunk import session_store
    from vegapunk.config import config

    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    request = ContextInput((Message(role="user", content=[TextBlock(text="Keep it private")]),
                            Message(role="assistant", content=[TextBlock(text="Observed possible issue")]),
                            Message(role="user", content=[TextBlock(text="Continue")]),),
                           "system", (ToolSpec("read_artifact", "retrieve", {}),), "fake", 1000)
    output = json.dumps({"notes": [{"text": "Possible issue remains unverified", "source": "1:0", "quote": "possible issue", "kind": "uncertainty"}]})
    child = FakeProvider([says(output)])
    backend = replace(backend_for(), spawn_provider=lambda: child)
    cfg = replace(config, context_recent_turns=1)
    async def run():
        first = await context_summary.summarize(request, request.messages, sid, cfg, lambda: backend)
        second = await context_summary.summarize(request, request.messages, sid, cfg, lambda: backend)
        return first, second
    first, second = asyncio.run(run())
    assert first[0] == (request.messages[0], request.messages[2])
    assert "source_artifact" in first[1]
    assert second[2]["summary_cache_hit"]
    assert child.call_count == 1


def test_small_summary_window_fails_before_spawning_provider():
    import asyncio
    from dataclasses import replace
    from tests.fake_provider import backend_for
    from logpose import ContextInput, Message, TextBlock
    from logpose.providers.base import ToolSpec
    from vegapunk import session_store
    from vegapunk.config import config

    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    messages = (Message(role="user", content=[TextBlock(text="Keep private")]),
                Message(role="assistant", content=[TextBlock(text="historical details")]),
                Message(role="user", content=[TextBlock(text="Continue")]))
    request = ContextInput(messages, "system", (ToolSpec("read_artifact", "retrieve", {}),), "fake", 100)
    backend = replace(backend_for(), context_window=1024, spawn_provider=lambda: pytest.fail("summary provider must not spawn"))
    with pytest.raises(ValueError, match="budget"):
        asyncio.run(context_summary.summarize(request, messages, sid, replace(config, context_recent_turns=1), lambda: backend))


@pytest.mark.parametrize("valid", [True, False])
def test_summary_retains_action_identity_and_rejected_output(valid):
    import asyncio
    from dataclasses import replace
    from tests.fake_provider import backend_for, says, FakeProvider
    from logpose import ContextInput, Message, TextBlock, ToolUseBlock, ToolResultBlock
    from logpose.providers.base import ToolSpec
    from vegapunk import artifacts, db, session_store
    from vegapunk.config import config

    session_store.save_session("one", [])
    sid = session_store.session_identity("one")
    messages = (Message(role="user", content=[TextBlock(text="Inspect service")]),
                Message(role="assistant", content=[ToolUseBlock(id="check", name="inspect", input={"target": "private-service"})]),
                Message(role="user", content=[ToolResultBlock(tool_use_id="check", content="connection refused", is_error=True)]),
                Message(role="user", content=[TextBlock(text="Do not retry writes")]))
    output = json.dumps({"notes": [{"text": "The check failed", "source": "2:0" if valid else "tool_result:2:0", "quote": "connection refused", "kind": "observation"}]})
    backend = replace(backend_for(), spawn_provider=lambda: FakeProvider([says(output)]))
    request = ContextInput(messages, "system", (ToolSpec("read_artifact", "retrieve", {}),), "fake", 100)
    call = context_summary.summarize(request, messages, sid, replace(config, context_recent_turns=1), lambda: backend)
    if valid:
        selected, system, _ = asyncio.run(call)
        notes = json.loads(system.rsplit("\n", 1)[-1])
        assert selected == (messages[0], messages[-1])
        assert notes["notes"][0]["source_is_error"]
        sources = json.loads(artifacts.read(sid, notes["source_artifact"])["text"])
        action = next(s for s in sources if s["kind"] == "tool_use")
        assert json.loads(action["text"]) == {"name": "inspect", "arguments": {"target": "private-service"}, "tool_call_id": "check"}
        assert next(s for s in sources if s["kind"] == "tool_result")["tool_call_id"] == "check"
    else:
        with pytest.raises(context_summary.SummaryValidationError) as caught:
            asyncio.run(call)
        assert artifacts.read(sid, caught.value.artifact_id)["text"] == output
        assert db.query("SELECT * FROM context_summaries WHERE session_id=?", (sid,)) == []

"""Plan history remains readable after completion without involving a model."""

import json
from prompt_toolkit.document import Document
from prompt_toolkit.completion import CompleteEvent
from vegapunk import progress, session_store
from vegapunk.commands import CommandContext, dispatch
from vegapunk.prompter import SlashCompleter


def test_plan_history_paginates_and_resolves_only_current_session():
    session_store.save_session("one", [])
    session_store.save_session("two", [])
    sid = session_store.session_identity("one")
    other = progress.create(session_store.session_identity("two"), "Other", ["inspect"])
    ids = set()
    for i in range(21):
        plan = progress.create(sid, f"Historical {i}", ["inspect"])
        ids.add(plan["id"])
        progress.update(sid, plan["id"], 0, "cancelled", 1)
    ctx = CommandContext(session=None, current_name="one")
    first = json.loads(dispatch("/plan history", ctx).output)
    assert len(first["items"]) == 20 and first["next_offset"] == 20
    assert len(json.loads(dispatch("/plan history 20", ctx).output)["items"]) == 1
    assert all(not item["active"] for item in first["items"])
    assert json.loads(dispatch(f"/plan show {plan['id']}", ctx).output)["id"] == plan["id"]
    assert other["id"] not in dispatch("/plan history", ctx).output
    assert "not found in this session" in dispatch(f"/plan show {other['id']}", ctx).output
    assert "Could not read plan" in dispatch("/plan history -1", ctx).output
    choices = {c.text for c in SlashCompleter(session_identity=lambda: sid).get_completions(Document("/plan show "), CompleteEvent())}
    assert choices == ids and other["id"] not in choices
    assert {c.text for c in SlashCompleter().get_completions(Document("/plan "), CompleteEvent())} == {"show", "history"}

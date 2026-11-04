"""Plan history remains readable after completion without involving a model."""

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
    first = dispatch("/plan history", ctx).output
    second = dispatch("/plan history 20", ctx).output
    assert first.count("[finished]") == 20 and "Next page: /plan history 20" in first
    assert second.count("[finished]") == 1 and "Next page:" not in second
    assert {item for item in ids if item in first or item in second} == ids
    assert "[active]" not in first
    detail = dispatch(f"/plan show {plan['id']}", ctx).output
    assert f"ID: {plan['id']}" in detail and "[cancelled] inspect" in detail
    assert other["id"] not in dispatch("/plan history", ctx).output
    assert "not found in this session" in dispatch(f"/plan show {other['id']}", ctx).output
    assert "Could not read plan" in dispatch("/plan history -1", ctx).output
    choices = {c.text for c in SlashCompleter(session_identity=lambda: sid).get_completions(Document("/plan show "), CompleteEvent())}
    assert choices == ids and other["id"] not in choices
    assert {c.text for c in SlashCompleter().get_completions(Document("/plan "), CompleteEvent())} == {"show", "history"}

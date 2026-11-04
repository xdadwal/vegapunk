"""Pinned and retrieved memory stays budgeted and distinct from session evidence."""

import pytest
from vegapunk import db, memory, memory_context


def test_pinned_and_relevant_facts_exclude_unrelated_or_superseded():
    memory.save_memory("Use private networking")
    memory.save_memory("Ohara runs Debian")
    memory.save_memory("I like tea")
    rows = {m.content: m.id for m in memory.list_memory()}
    memory_context.pin(rows["Use private networking"], True)
    selected = memory_context.select("Ohara", 1200)
    assert {m.content for m in selected} == {"Use private networking", "Ohara runs Debian"}
    memory_context.supersede(rows["Ohara runs Debian"], rows["I like tea"])
    assert {m.content for m in memory_context.select("Ohara", 1200)} == {"Use private networking"}
    assert "Ohara runs Debian" not in memory.load_memory()
    assert memory.recall_memory("Ohara") == []


def test_pinned_budget_overflow_is_explicit():
    memory.save_memory("standing preference")
    memory_context.pin(memory.list_memory()[0].id, True)
    with pytest.raises(ValueError, match="Pinned memory"):
        memory_context.select("", 1)


def test_staged_skill_does_not_replace_the_memory_query(monkeypatch):
    from dataclasses import replace
    from vegapunk import cli, prompt
    from vegapunk.commands import CommandResult
    from vegapunk.prompter import ScriptedPrompter
    from tests.fake_provider import backend_for, says

    memory.save_memory("Ohara runs Debian")
    monkeypatch.setattr(cli, "config", replace(cli.config, context_policy="bounded"))
    backend = backend_for([says("done")], title="Ohara task")
    monkeypatch.setattr(cli, "create_backend", lambda *args, **kwargs: backend)
    dispatch = cli.dispatch
    def staged(line, ctx):
        if line == "/stage":
            ctx.pending_skill = ("workflow", "Follow elaborate engineering instructions and inspect artifacts before conducting analysis. " * 20)
            return CommandResult()
        return dispatch(line, ctx)
    monkeypatch.setattr(cli, "dispatch", staged)
    cli.main(prompter=ScriptedPrompter(["/stage", "Configure Ohara", "/exit"]))
    assert "Ohara runs Debian" in backend.provider.last_request.system

"""Removing an integration must not widen old tasks' execution authority."""

import pytest

from tests.fake_provider import agent_for, says
from vegapunk import db, scheduler


def test_fresh_database_does_not_install_platform_storage():
    names = {row[0] for row in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(name.startswith("moltbook_") for name in names)
    assert {"scheduled_tasks", "scheduled_runs", "scheduled_run_events"} <= names


def test_registry_does_not_expose_removed_integration_tools():
    from vegapunk.tools import ALL_TOOLS
    assert not any(tool.name.startswith("moltbook_") for tool in ALL_TOOLS)


@pytest.mark.parametrize("profile", ["moltbook", "retired_integration"])
def test_upgrade_disables_specialized_tasks_without_deleting_data(profile):
    scheduler.add_task("Old specialized task", 300)
    task = scheduler.list_tasks()[0]
    db.execute("UPDATE scheduled_tasks SET profile=?,last_result='Last retained summary' WHERE id=?", (profile, task.id))
    db.execute("CREATE TABLE archived_notes (body TEXT)")
    db.execute("INSERT INTO archived_notes VALUES ('Retain existing learning')")
    db.execute("UPDATE meta SET value='14' WHERE key='schema_version'")
    db.close_connection()
    task = scheduler.list_tasks()[0]
    assert not task.enabled
    assert task.last_status == "retired"
    assert task.last_result == "Last retained summary"
    assert db.query("SELECT body FROM archived_notes") == [("Retain existing learning",)]
    assert scheduler.due_tasks(now=db.utcnow_plus(600)) == []


def test_retired_profile_never_runs_even_if_manually_enabled():
    scheduler.add_task("Do not run with regular tools", 300)
    task = scheduler.list_tasks()[0]
    db.execute("UPDATE scheduled_tasks SET profile='retired_integration',enabled=1 WHERE id=?", (task.id,))
    agent, provider = agent_for(says("must not run"))
    scheduler.run_task(task, agent)
    assert provider.requests == []
    assert scheduler.due_tasks(now=db.utcnow_plus(600)) == []

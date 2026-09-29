"""Social runs cannot retrieve personal context or escape their tool profile."""

from vegapunk import db, scheduler
from tests.fake_provider import agent_for, call, says, wants


def test_profile_is_explicit_and_survives_reopen():
    scheduler.add_task("Moltbook in ordinary text", 1800)
    scheduler.add_task("Explore", 1800, profile="moltbook")
    db.close_connection()
    assert [task.profile for task in scheduler.list_tasks()] == ["general", "moltbook"]


def test_social_run_replaces_private_prompt_and_tools():
    from vegapunk.tools import ALL_TOOLS
    scheduler.add_task("Explore public discussions", 1800, profile="moltbook")
    task = scheduler.list_tasks()[0]
    agent, provider = agent_for([
        wants(call("recall", {"query": "user"}), call("remember", {"fact": "poison"}),
              call("read_file", {"path": "README.md"}), call("fetch_url", {"url": "http://localhost"}),
              call("schedule_task", {"prompt": "escape", "interval_seconds": 60})), says("done")],
        tools=ALL_TOOLS, system="PRIVATE PERSONAL CONTEXT")
    scheduler.run_task(task, agent)
    request = provider.requests[0]
    assert "PRIVATE PERSONAL CONTEXT" not in request.system
    assert {tool.name for tool in request.tools} == {
        "moltbook_home", "moltbook_feed", "moltbook_post", "moltbook_comments", "moltbook_search", "moltbook_submolts",
        "moltbook_note", "moltbook_notebook", "moltbook_complete_note", "moltbook_reply", "moltbook_verify_reply"}
    assert db.query("SELECT COUNT(*) FROM memory") == [(0,)]
    assert len(scheduler.list_tasks()) == 1
    assert agent.system == "PRIVATE PERSONAL CONTEXT"


def test_conversion_quarantines_old_summaries_and_revokes_grant():
    scheduler.add_task("Explore", 1800)
    task = scheduler.list_tasks()[0]
    agent, _ = agent_for(says("PRIVATE OLD SUMMARY"))
    scheduler.run_task(task, agent)
    db.execute("INSERT INTO moltbook_permissions VALUES (?,?,?)", (task.id, "account", db.utcnow()))
    assert "moltbook" in scheduler.set_profile(task.id, "moltbook")
    task = scheduler.list_tasks()[0]
    agent, provider = agent_for(says("public observation"))
    scheduler.run_task(task, agent)
    assert "PRIVATE OLD SUMMARY" not in str(provider.requests)
    assert db.query("SELECT COUNT(*) FROM moltbook_permissions") == [(0,)]
    assert db.query("SELECT result FROM scheduled_runs ORDER BY started_at")[0] == ("PRIVATE OLD SUMMARY",)


def test_general_profile_cannot_get_a_reply_grant():
    import pytest
    from vegapunk import moltbook_actions
    scheduler.add_task("ordinary", 1800)
    with pytest.raises(moltbook_actions.ActionBlocked, match="profile"):
        moltbook_actions.grant(scheduler.list_tasks()[0].id, "account")


def test_alternating_profiles_keep_distinct_prompts_and_tools():
    from vegapunk.tools import ALL_TOOLS
    scheduler.add_task("social", 1800, profile="moltbook")
    scheduler.add_task("ordinary", 1800)
    social, general = scheduler.list_tasks()
    agent, provider = agent_for([says("public"), says("private")], tools=ALL_TOOLS, system="PERSONAL MEMORY")
    scheduler.run_task(social, agent)
    scheduler.run_task(general, agent)
    assert "PERSONAL MEMORY" not in provider.requests[0].system
    assert "PERSONAL MEMORY" in provider.requests[1].system
    assert "recall" not in {tool.name for tool in provider.requests[0].tools}
    assert "recall" in {tool.name for tool in provider.requests[1].tools}


def test_profiles_share_one_provider_loop_and_close_once():
    import asyncio
    from logpose import Agent
    from tests.fake_provider import FakeProvider
    from vegapunk.task_profiles import close_scheduled_agent

    class LoopBoundProvider(FakeProvider):
        bound_loop = None
        closes = 0

        async def stream(self, request):
            running = asyncio.get_running_loop()
            if self.bound_loop is None:
                self.bound_loop = running
            assert running is self.bound_loop, "provider crossed event loops"
            async for event in super().stream(request):
                yield event

        async def aclose(self):
            assert asyncio.get_running_loop() is self.bound_loop
            self.closes += 1

    scheduler.add_task("ordinary", 1800)
    scheduler.add_task("social", 1800, profile="moltbook")
    general, social = scheduler.list_tasks()
    provider = LoopBoundProvider([says("one"), says("two"), says("three")])
    agent = Agent(provider, system="private")
    try:
        assert scheduler.run_task(general, agent) == "one"
        assert scheduler.run_task(social, agent) == "two"
        assert scheduler.run_task(general, agent) == "three"
    finally:
        close_scheduled_agent(agent)
        close_scheduled_agent(agent)
    assert provider.closes == 1
    assert provider.bound_loop.is_closed()


def test_private_extra_and_observers_are_not_inherited():
    from vegapunk.task_profiles import isolated_agent
    agent, _ = agent_for(says("done"))
    agent.extra = {"reasoning": {"effort": "high", "private": "secret"},
                   "metadata": {"user": "private"}}
    agent.observers = (object(),)
    restricted = isolated_agent(agent)
    assert restricted.extra == {"reasoning": {"effort": "high", "summary": "auto"}}
    assert restricted.observers == ()


def test_profile_change_during_startup_blocks_stale_configuration(monkeypatch):
    from vegapunk import task_history
    scheduler.add_task("Explore", 1800)
    task = scheduler.list_tasks()[0]
    original = task_history.begin

    def transition_then_begin(snapshot):
        scheduler.set_profile(task.id, "moltbook")
        return original(snapshot)

    monkeypatch.setattr(task_history, "begin", transition_then_begin)
    agent, provider = agent_for(says("must not run"), system="private")
    assert "profile changed" in scheduler.run_task(task, agent)
    assert provider.requests == []
    assert task_history.list_runs(task.id) == []


def test_profile_commands_and_active_run_refusal():
    from vegapunk import task_history
    from vegapunk.commands import CommandContext, dispatch
    from tests.fake_provider import session_for
    ctx = CommandContext(session=session_for())
    dispatch("/schedule add 1800 --profile moltbook Explore public discussions", ctx)
    task = scheduler.list_tasks()[0]
    assert task.profile == "moltbook" and task.prompt == "Explore public discussions"
    assert "profile=moltbook" in dispatch("/schedule list", ctx).output
    run_id = task_history.begin(task)
    assert "active run" in dispatch(f"/schedule profile {task.id} general", ctx).output
    task_history.finish(task, run_id, "completed", "done")
    assert "general" in dispatch(f"/schedule profile {task.id} general", ctx).output


def test_general_authenticated_reader_is_blocked_before_credentials(monkeypatch):
    from vegapunk.tools import moltbook
    def unavailable(*args):
        raise AssertionError("general tasks must not load Moltbook credentials")
    monkeypatch.setattr(moltbook, "_load_api_key", unavailable)
    scheduler.add_task("Explore moltbook", 1800)
    agent, provider = agent_for([wants(call("moltbook_home")), says("done")], tools=[moltbook.moltbook_home])
    scheduler.run_task(scheduler.list_tasks()[0], agent)
    assert "active moltbook profile" in str(provider.last_request.messages)


def test_conversion_quarantines_notebook_and_sources_but_keeps_human_history(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from vegapunk import moltbook_notebook
    from vegapunk.tools import moltbook, moltbook_notebook as tools
    keyfile = tmp_path / "credential.json"
    keyfile.write_text('{"api_key":"privacy-test-key"}')
    monkeypatch.setattr(moltbook, "config", replace(moltbook.config, moltbook_credentials_file=keyfile))
    monkeypatch.setattr(moltbook, "_get", lambda *args, **kwargs: SimpleNamespace(
        status_code=200, headers={}, raise_for_status=lambda: None,
        json=lambda: {"post": {"id": "post", "content": "Public quote."}}))
    scheduler.add_task("explore", 1800, profile="moltbook")
    task = scheduler.list_tasks()[0]
    def read():
        agent, _ = agent_for([wants(call("moltbook_post", {"post_id": "post"})), says("read")], tools=[moltbook.moltbook_post])
        scheduler.run_task(task, agent)
    read()
    source = db.query("SELECT id FROM moltbook_sources")[0][0]
    agent, _ = agent_for([wants(call("moltbook_note", {"kind": "hypothesis", "subject": "old context",
        "text": "PRIVATE OLD NOTE", "source_id": source, "quote": "Public quote."})), says("saved")], tools=[tools.moltbook_note])
    scheduler.run_task(task, agent)
    scheduler.set_profile(task.id, "general")
    scheduler.set_profile(task.id, "moltbook")
    agent, provider = agent_for([wants(call("moltbook_notebook", {"source_id": source})), says("done")], tools=[tools.moltbook_notebook])
    scheduler.run_task(task, agent)
    assert "PRIVATE OLD NOTE" not in str(provider.requests)
    assert source not in str(provider.last_request.messages[-1])
    assert "PRIVATE OLD NOTE" in moltbook_notebook.format_notebook(task.id)
    read()
    assert db.query("SELECT COUNT(*) FROM moltbook_sources") == [(2,)]
    agent, provider = agent_for([wants(call("moltbook_note", {"kind": "hypothesis", "subject": "reuse",
        "text": "reuse old evidence", "source_id": source, "quote": "Public quote."})), says("done")], tools=[tools.moltbook_note])
    scheduler.run_task(task, agent)
    assert "Blocked:" in str(provider.last_request.messages)
    assert db.query("SELECT COUNT(*) FROM moltbook_notes") == [(1,)]


def test_v8_migration_retains_legacy_tasks_without_enabling_social_access():
    db.execute("DROP TABLE scheduled_tasks")
    db.execute("CREATE TABLE scheduled_tasks (id TEXT PRIMARY KEY,prompt TEXT NOT NULL,interval_seconds INTEGER NOT NULL,"
               "next_run_at TEXT NOT NULL,last_run_at TEXT,last_status TEXT,last_result TEXT,enabled INTEGER NOT NULL DEFAULT 1,created_at TEXT NOT NULL)")
    db.execute("INSERT INTO scheduled_tasks (id,prompt,interval_seconds,next_run_at,created_at) VALUES ('legacy','old',1800,?,?)",
               (db.utcnow(), db.utcnow()))
    db.execute("UPDATE meta SET value='8' WHERE key='schema_version'")
    db.close_connection()
    task = scheduler.list_tasks()[0]
    assert task.id == "legacy" and task.profile == "general" and task.profile_since == ""
    assert db.query("SELECT value FROM meta WHERE key='schema_version'") == [("9",)]


def test_credential_errors_do_not_expose_local_paths(tmp_path, monkeypatch):
    from dataclasses import replace
    from vegapunk.tools import moltbook
    missing = tmp_path / "PRIVATE_USER_DIRECTORY" / "missing.json"
    monkeypatch.setattr(moltbook, "config", replace(moltbook.config, moltbook_credentials_file=missing))
    scheduler.add_task("public exploration", 1800, profile="moltbook")
    agent, provider = agent_for([wants(call("moltbook_home")), says("blocked")], tools=[moltbook.moltbook_home])
    scheduler.run_task(scheduler.list_tasks()[0], agent)
    assert "PRIVATE_USER_DIRECTORY" not in str(provider.requests)
    assert "credentials unavailable" in str(provider.last_request.messages)


def test_old_reply_cannot_be_verified_after_profile_boundary():
    import pytest
    from vegapunk import moltbook_actions as actions, task_history
    scheduler.add_task("explore", 1800, profile="moltbook")
    task = scheduler.list_tasks()[0]
    actions.grant(task.id, "account")
    run = task_history.begin(task)
    scope = actions.Execution(task.id, run, profile_since=task.profile_since)
    action = actions.reserve(scope, "account", "post", "parent", "old outgoing content")
    actions.complete(action, "sending", "pending_verification", code="secret-code", remote_id="remote",
                     challenge="OLD CHALLENGE", expires="2099-01-01T00:00:00.000000Z")
    task_history.finish(task, run, "partial", "old run")
    scheduler.set_profile(task.id, "general")
    scheduler.set_profile(task.id, "moltbook")
    task = scheduler.list_tasks()[0]
    actions.grant(task.id, "account")
    run = task_history.begin(task)
    scope = actions.Execution(task.id, run, profile_since=task.profile_since)
    with pytest.raises(actions.ActionBlocked, match="no pending verification"):
        actions.verification(scope, action, "account")
    with pytest.raises(actions.ActionBlocked, match="unresolved reply"):
        actions.reserve(scope, "account", "another-post", "parent", "new outgoing content")
    assert "OLD CHALLENGE" not in actions.context(task.id)
    assert actions.list_actions(task.id)[0]["challenge"] == "OLD CHALLENGE"

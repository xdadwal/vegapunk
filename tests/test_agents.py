"""Personality selection reuses the existing conversation and persistence."""
from __future__ import annotations

import sqlite3
from dataclasses import replace

import pytest

from vegapunk import db, prompt, session_store
from vegapunk.backend import Backend, current_effort
from tests.fake_provider import FakeProvider
from vegapunk.cli import _autosave_turn, _status_line, main
from vegapunk.commands import CommandContext, dispatch
from vegapunk.config import config
from vegapunk.prompter import ScriptedPrompter, _argument_options
from tests.fake_provider import says, session_for, user_turn


@pytest.fixture(autouse=True)
def fake_backends(monkeypatch):
    def create(name, cfg):
        provider = FakeProvider()
        provider.name = name
        model = cfg.codex_model if name in ('codex', 'openai') else cfg.model
        return Backend(provider, model, 0, effort_key='reasoning' if name in ('codex', 'openai') else '')
    monkeypatch.setattr('vegapunk.commands.create_backend', create)

NAMES = ('shaka', 'lilith', 'edison', 'pythagoras', 'atlas', 'york')


def test_agent_switch_applies_defaults_and_keeps_history():
    ctx = CommandContext(session_for())
    ctx.session.restore([user_turn('An earlier thought')])
    backend = ctx.session.backend
    assert all(name in dispatch('/agent', ctx).output.lower() for name in NAMES)
    assert 'Unknown command' not in dispatch('/agent LiLiTh', ctx).output
    assert ctx.agent_id == 'lilith'
    assert ctx.session.backend is not backend
    assert ctx.session.model_label == 'gpt-5.5'
    assert current_effort(ctx.session.backend) == 'medium'
    assert ctx.session.messages == [user_turn('An earlier thought')]
    assert 'Lilith' in _status_line(ctx)
    assert 'lilith' in _argument_options('agent', [''])
    dispatch('/journal', ctx)
    assert ctx.agent_id == 'lilith'
    dispatch('/new', ctx)
    assert ctx.agent_id == 'lilith'


def test_agent_invalid_name_does_not_change_state():
    ctx = CommandContext(session_for())
    dispatch('/agent shaka', ctx)
    assert 'Unknown agent' in dispatch('/agent missing', ctx).output
    assert ctx.agent_id == 'shaka'


def test_agent_save_switch_rename_resume_and_autosave():
    ctx = CommandContext(session_for())
    dispatch('/agent edison', ctx)
    ctx.session.restore([user_turn('An idea')])
    dispatch('/save ideas', ctx)
    dispatch('/agent york', ctx)
    assert session_store.load_session_agent('ideas') == 'york'
    dispatch('/save renamed', ctx)
    assert session_store.load_session_agent('renamed') == 'york'
    dispatch('/new', ctx)
    dispatch('/agent default', ctx)
    dispatch('/sessions renamed', ctx)
    assert ctx.agent_id == 'york'
    _autosave_turn(ctx)
    assert session_store.load_session_agent('renamed') == 'york'
    session_store.rename_session('renamed', 'again', ctx.session.messages)
    session_store.rename_session('again', 'again', ctx.session.messages)
    assert session_store.load_session_agent('again') == 'york'


def test_failed_agent_write_keeps_active_agent(monkeypatch):
    ctx = CommandContext(session_for())
    dispatch('/save saved', ctx)

    def fail(*args, **kwargs):
        raise db.StoreError('disk unavailable')

    monkeypatch.setattr(db, 'execute', fail)
    assert 'Could not' in dispatch('/agent atlas', ctx).output
    assert ctx.agent_id == 'default'


@pytest.mark.parametrize('agent_id', NAMES)
def test_agent_prompt_keeps_policies_and_journal_boundaries(agent_id):
    regular = prompt.system_prompt(config, mode='manual', agent_id=agent_id)
    assert f'Agent: {agent_id.title()}' in regular
    assert config.system_prompt in regular
    assert 'Approval mode: manual' in regular
    journal = prompt.system_prompt(config, mode='manual', conversation_mode='journal', agent_id=agent_id)
    assert f'Agent: {agent_id.title()}' in journal
    assert config.system_prompt not in journal
    assert 'Journal instructions take precedence' in journal


def test_injected_session_switches_agents_and_returns_to_custom_prompt(monkeypatch):
    session = session_for([says('First'), says('Second'), says('Third')], system_prompt='CUSTOM')
    provider = session.backend.provider
    monkeypatch.setattr('vegapunk.commands.create_backend',
                        lambda *args: replace(session.backend, model_label='gpt-5.5', effort_key='reasoning'))
    main(prompter=ScriptedPrompter(['/agent shaka', 'Hello', '/agent atlas', 'Continue',
                                  '/agent default', 'Back', '/exit']), session=session)
    assert 'Agent: Shaka' in provider.requests[0].system
    assert 'Agent: Atlas' in provider.requests[1].system
    assert provider.requests[2].system == 'CUSTOM'
    assert session_store.load_session_agent('scripted-title') == 'default'


def test_v3_journal_database_migrates_to_default_agent():
    path = db.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO meta VALUES ('schema_version','3');
            CREATE TABLE sessions (slug TEXT PRIMARY KEY,messages TEXT NOT NULL,turns INTEGER NOT NULL,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                conversation_mode TEXT NOT NULL DEFAULT 'conversation');
            INSERT INTO sessions VALUES ('entry','[]',0,'created','updated','journal');
        ''')
    assert session_store.load_session_agent('entry') == 'default'
    assert session_store.load_session_mode('entry') == 'journal'
    db.close_connection()
    assert session_store.load_session_agent('entry') == 'default'


def test_corrupt_saved_agent_does_not_replace_current_conversation():
    ctx = CommandContext(session_for())
    dispatch('/agent shaka', ctx)
    ctx.session.restore([user_turn('Keep this')])
    session_store.save_session('broken', [])
    db.execute("UPDATE sessions SET agent_id='unknown' WHERE slug='broken'")
    assert 'Could not load' in dispatch('/sessions broken', ctx).output
    assert ctx.agent_id == 'shaka'
    assert ctx.session.messages == [user_turn('Keep this')]

@pytest.mark.parametrize(('name', 'effort'), [
    ('shaka', 'high'), ('pythagoras', 'high'), ('edison', 'medium'),
    ('lilith', 'medium'), ('atlas', 'low'), ('york', 'low'),
])
def test_agent_defaults_and_profile_alias(name, effort):
    ctx = CommandContext(session_for())
    dispatch(f'/agent {name}', ctx)
    assert ctx.agent_id == name
    assert ctx.session.backend.provider.name == 'codex'
    assert ctx.session.model_label == 'gpt-5.5'
    assert current_effort(ctx.session.backend) == effort
    dispatch('/effort xhigh', ctx)
    dispatch(f'/profile {name}', ctx)
    assert current_effort(ctx.session.backend) == effort


def test_model_and_effort_overrides_persist_and_agent_selection_resets():
    ctx = CommandContext(session_for())
    dispatch('/agent shaka', ctx)
    ctx.session.restore([user_turn('Remember this conversation')])
    dispatch('/save settings', ctx)
    before = db.query("SELECT updated_at FROM sessions WHERE slug='settings'")
    dispatch('/model codex gpt-5.4', ctx)
    dispatch('/effort low', ctx)
    assert db.query("SELECT updated_at FROM sessions WHERE slug='settings'") == before
    assert session_store.load_session_execution('settings') == ('codex:gpt-5.4', 'low')
    dispatch('/save renamed', ctx)
    dispatch('/new', ctx)
    dispatch('/agent york', ctx)
    dispatch('/sessions renamed', ctx)
    assert ctx.agent_id == 'shaka'
    assert ctx.session.model_label == 'gpt-5.4'
    assert current_effort(ctx.session.backend) == 'low'
    _autosave_turn(ctx)
    session_store.rename_session('renamed', 'again', ctx.session.messages)
    session_store.rename_session('again', 'again', ctx.session.messages)
    assert session_store.load_session_execution('again') == ('codex:gpt-5.4', 'low')
    dispatch('/agent shaka', ctx)
    assert ctx.session.model_label == 'gpt-5.5'
    assert current_effort(ctx.session.backend) == 'high'


@pytest.mark.parametrize('command', ['/agent york', '/model codex gpt-5.4', '/effort low'])
def test_failed_settings_write_preserves_backend_and_agent(monkeypatch, command):
    ctx = CommandContext(session_for())
    dispatch('/agent shaka', ctx)
    dispatch('/save existing', ctx)
    original = ctx.session.backend
    def fail(*args, **kwargs):
        raise db.StoreError('disk unavailable')
    monkeypatch.setattr(db, 'execute', fail)
    assert 'Could not' in dispatch(command, ctx).output
    assert ctx.session.backend is original
    assert ctx.agent_id == 'shaka'


def test_corrupt_execution_settings_leave_conversation_intact():
    ctx = CommandContext(session_for())
    dispatch('/agent shaka', ctx)
    ctx.session.restore([user_turn('Keep me')])
    original = ctx.session.backend
    session_store.save_session('bad', [])
    db.execute("UPDATE sessions SET model_selector='nonexistent:model',effort='low' WHERE slug='bad'")
    assert 'Could not' in dispatch('/sessions bad', ctx).output
    assert ctx.session.backend is original
    assert ctx.session.messages == [user_turn('Keep me')]
    assert ctx.agent_id == 'shaka'


def test_v4_migration_preserves_named_agent():
    path = db.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
            INSERT INTO meta VALUES ('schema_version','4');
            CREATE TABLE sessions (slug TEXT PRIMARY KEY,messages TEXT NOT NULL,turns INTEGER NOT NULL,
                created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
                conversation_mode TEXT NOT NULL DEFAULT 'conversation',
                profile TEXT NOT NULL DEFAULT 'default');
            INSERT INTO sessions VALUES ('entry','[]',0,'created','updated','journal','edison');
        """)
    assert session_store.load_session_agent('entry') == 'edison'
    assert session_store.load_session_execution('entry') == ('', '')
    ctx = CommandContext(session_for())
    dispatch('/sessions entry', ctx)
    assert ctx.agent_id == 'edison'
    assert ctx.conversation_mode == 'journal'
    assert current_effort(ctx.session.backend) == 'medium'


def test_backend_resolution_failure_preserves_saved_and_live_selection(monkeypatch):
    ctx = CommandContext(session_for())
    dispatch('/agent shaka', ctx)
    dispatch('/save unchanged', ctx)
    original = ctx.session.backend
    def fail(*args):
        raise ValueError('model unavailable')
    monkeypatch.setattr('vegapunk.commands.create_backend', fail)
    assert 'Could not' in dispatch('/agent default', ctx).output
    assert ctx.session.backend is original
    assert ctx.agent_id == session_store.load_session_agent('unchanged') == 'shaka'


def test_unsupported_effort_model_override_round_trips():
    ctx = CommandContext(session_for())
    dispatch('/agent shaka', ctx)
    dispatch('/model local ai/qwen3', ctx)
    assert current_effort(ctx.session.backend) == ''
    assert 'no effort setting' in dispatch('/effort high', ctx).output
    dispatch('/save local-settings', ctx)
    dispatch('/new', ctx)
    dispatch('/agent york', ctx)
    dispatch('/sessions local-settings', ctx)
    assert ctx.agent_id == 'shaka'
    assert ctx.session.model_label == 'ai/qwen3'
    assert current_effort(ctx.session.backend) == ''


def test_invalid_messages_do_not_install_saved_backend(monkeypatch):
    ctx = CommandContext(session_for())
    dispatch('/agent shaka', ctx)
    ctx.session.restore([user_turn('Keep this')])
    original = ctx.session.backend
    session_store.save_session('broken-messages', [{'role': 'user', 'content': [{'type': 'unknown'}]}],
                               model_selector='codex:gpt-5.4', effort='low')
    assert 'Could not resume' in dispatch('/sessions broken-messages', ctx).output
    assert ctx.session.backend is original
    assert ctx.session.messages == [user_turn('Keep this')]

"""Personality selection reuses the existing conversation and persistence."""
from __future__ import annotations

import sqlite3

import pytest

from vegapunk import db, prompt, session_store
from vegapunk.cli import _autosave_turn, _status_line, main
from vegapunk.commands import CommandContext, dispatch
from vegapunk.config import config
from vegapunk.prompter import ScriptedPrompter, _argument_options
from tests.fake_provider import says, session_for, user_turn

NAMES = ('shaka', 'lilith', 'edison', 'pythagoras', 'atlas', 'york')


def test_profile_switch_keeps_backend_and_history_and_is_visible():
    ctx = CommandContext(session_for())
    ctx.session.restore([user_turn('An earlier thought')])
    backend = ctx.session.backend
    assert all(name in dispatch('/profile', ctx).output.lower() for name in NAMES)
    assert 'Unknown command' not in dispatch('/profile LiLiTh', ctx).output
    assert ctx.profile == 'lilith'
    assert ctx.session.backend is backend
    assert ctx.session.messages == [user_turn('An earlier thought')]
    assert 'Lilith' in _status_line(ctx)
    assert 'lilith' in _argument_options('profile', [''])
    dispatch('/journal', ctx)
    assert ctx.profile == 'lilith'
    dispatch('/new', ctx)
    assert ctx.profile == 'lilith'


def test_profile_invalid_name_does_not_change_state():
    ctx = CommandContext(session_for())
    dispatch('/profile shaka', ctx)
    assert 'Unknown profile' in dispatch('/profile missing', ctx).output
    assert ctx.profile == 'shaka'


def test_profile_save_switch_rename_resume_and_autosave():
    ctx = CommandContext(session_for())
    dispatch('/profile edison', ctx)
    ctx.session.restore([user_turn('An idea')])
    dispatch('/save ideas', ctx)
    dispatch('/profile york', ctx)
    assert session_store.load_session_profile('ideas') == 'york'
    dispatch('/save renamed', ctx)
    assert session_store.load_session_profile('renamed') == 'york'
    dispatch('/new', ctx)
    dispatch('/profile default', ctx)
    dispatch('/sessions renamed', ctx)
    assert ctx.profile == 'york'
    _autosave_turn(ctx)
    assert session_store.load_session_profile('renamed') == 'york'
    session_store.rename_session('renamed', 'again', ctx.session.messages)
    session_store.rename_session('again', 'again', ctx.session.messages)
    assert session_store.load_session_profile('again') == 'york'


def test_failed_profile_write_keeps_active_profile(monkeypatch):
    ctx = CommandContext(session_for())
    dispatch('/save saved', ctx)

    def fail(*args, **kwargs):
        raise db.StoreError('disk unavailable')

    monkeypatch.setattr(db, 'execute', fail)
    assert 'Could not' in dispatch('/profile atlas', ctx).output
    assert ctx.profile == 'default'


@pytest.mark.parametrize('profile', NAMES)
def test_profile_prompt_keeps_policies_and_journal_boundaries(profile):
    regular = prompt.system_prompt(config, mode='manual', profile=profile)
    assert f'Personality profile: {profile.title()}' in regular
    assert config.system_prompt in regular
    assert 'Approval mode: manual' in regular
    journal = prompt.system_prompt(config, mode='manual', conversation_mode='journal', profile=profile)
    assert f'Personality profile: {profile.title()}' in journal
    assert config.system_prompt not in journal
    assert 'Journal instructions take precedence' in journal


def test_injected_session_switches_profiles_and_returns_to_custom_prompt():
    session = session_for([says('First'), says('Second'), says('Third')], system_prompt='CUSTOM')
    provider = session.backend.provider
    main(prompter=ScriptedPrompter(['/profile shaka', 'Hello', '/profile atlas', 'Continue',
                                  '/profile default', 'Back', '/exit']), session=session)
    assert 'Personality profile: Shaka' in provider.requests[0].system
    assert 'Personality profile: Atlas' in provider.requests[1].system
    assert provider.requests[2].system == 'CUSTOM'
    assert session_store.load_session_profile('scripted-title') == 'default'


def test_v3_journal_database_migrates_to_default_profile():
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
    assert session_store.load_session_profile('entry') == 'default'
    assert session_store.load_session_mode('entry') == 'journal'
    db.close_connection()
    assert session_store.load_session_profile('entry') == 'default'


def test_corrupt_saved_profile_does_not_replace_current_conversation():
    ctx = CommandContext(session_for())
    dispatch('/profile shaka', ctx)
    ctx.session.restore([user_turn('Keep this')])
    session_store.save_session('broken', [])
    db.execute("UPDATE sessions SET profile='unknown' WHERE slug='broken'")
    assert 'Could not load' in dispatch('/sessions broken', ctx).output
    assert ctx.profile == 'shaka'
    assert ctx.session.messages == [user_turn('Keep this')]

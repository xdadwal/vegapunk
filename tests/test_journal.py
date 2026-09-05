"""Journal behavior and persistence, with synthetic entries and no network."""
from __future__ import annotations

import sqlite3

import pytest

from vegapunk import db, prompt, session_store
from vegapunk.cli import _autosave_turn, _status_line, main
from vegapunk.commands import CommandContext, dispatch
from vegapunk.config import config
from vegapunk.prompter import ScriptedPrompter
from tests.fake_provider import backend_for, says, session_for, user_turn


def test_journal_prompt_replaces_task_instructions_and_skill_ads(monkeypatch):
    monkeypatch.setattr(prompt.memory, 'as_system_block', lambda: '\nMEMORY')
    monkeypatch.setattr(prompt.skills, 'as_system_block', lambda: '\nSKILLS')
    composed = prompt.system_prompt(config, mode='manual', conversation_mode='journal')
    assert 'journal' in composed.lower()
    assert config.system_prompt not in composed
    assert 'ask_user is mandatory' not in composed
    assert 'MEMORY' in composed and 'SKILLS' not in composed
    assert "require the user's approval" in composed


def test_journal_command_starts_fresh_and_new_returns_to_conversation():
    ctx = CommandContext(session_for())
    ctx.session.restore([user_turn('Previous task')])
    ctx.current_name = 'previous'
    ctx.pending_skill = ('task', 'Plan and act')
    result = dispatch('/journal', ctx)
    assert 'Unknown command' not in result.output
    assert ctx.conversation_mode == 'journal'
    assert ctx.session.messages == []
    assert ctx.current_name is None and ctx.pending_skill is None
    assert 'journal' in _status_line(ctx)
    dispatch('/new', ctx)
    assert ctx.conversation_mode == 'conversation'


def test_invalid_journal_arguments_do_not_reset_current_entry():
    ctx = CommandContext(session_for())
    ctx.session.restore([user_turn('Keep this')])
    assert 'Usage: /journal' in dispatch('/journal unexpected', ctx).output
    assert ctx.session.messages == [user_turn('Keep this')]


def test_journal_save_rename_resume_and_autosave_preserve_mode():
    ctx = CommandContext(session_for())
    dispatch('/journal', ctx)
    ctx.session.restore([user_turn('A quiet afternoon')])
    dispatch('/save afternoon', ctx)
    dispatch('/save renamed', ctx)
    assert session_store.load_session_mode('renamed') == 'journal'
    dispatch('/new', ctx)
    assert 'Resumed' in dispatch('/sessions renamed', ctx).output
    assert ctx.conversation_mode == 'journal'
    _autosave_turn(ctx)
    assert session_store.load_session_mode('renamed') == 'journal'
    assert session_store.load_session('renamed') == [user_turn('A quiet afternoon')]
    dispatch('/new', ctx)
    dispatch('/save regular', ctx)
    dispatch('/journal', ctx)
    dispatch('/sessions regular', ctx)
    assert ctx.conversation_mode == 'conversation'


def test_first_journal_autosave_persists_mode():
    ctx = CommandContext(session_for(turns=says('Quiet afternoon')))
    dispatch('/journal', ctx)
    ctx.session.restore([user_turn('A quiet afternoon')])
    _autosave_turn(ctx)
    assert ctx.current_name
    assert session_store.load_session_mode(ctx.current_name) == 'journal'


def test_failed_resume_keeps_current_mode_and_history():
    ctx = CommandContext(session_for())
    dispatch('/journal', ctx)
    ctx.session.restore([user_turn('Keep this entry')])
    session_store.save_session('broken', [{'role': 'invalid', 'content': []}])
    assert 'Could not resume' in dispatch('/sessions broken', ctx).output
    assert ctx.conversation_mode == 'journal'
    assert ctx.session.messages == [user_turn('Keep this entry')]


@pytest.mark.parametrize('version', [1, 2])
def test_older_database_migrates_sessions_as_regular_conversations(version):
    path = db.db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript('''
            CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE sessions (slug TEXT PRIMARY KEY, messages TEXT NOT NULL,
                turns INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            INSERT INTO sessions VALUES ('existing', '[]', 0, 'created', 'updated');
        ''')
        conn.execute("INSERT INTO meta VALUES ('schema_version', ?)", (str(version),))
    assert session_store.load_session_mode('existing') == 'conversation'
    assert session_store.load_session('existing') == []
    assert db.query("SELECT created_at,updated_at FROM sessions WHERE slug='existing'") == [('created', 'updated')]
    db.close_connection()
    assert session_store.load_session_mode('existing') == 'conversation'


def test_corrupt_mode_is_reported_without_switching_conversations():
    ctx = CommandContext(session_for())
    session_store.save_session('bad-mode', [])
    db.execute("UPDATE sessions SET conversation_mode='unknown' WHERE slug='bad-mode'")
    assert 'Could not load' in dispatch('/sessions bad-mode', ctx).output
    assert ctx.current_name is None


def test_cli_journal_prompt_survives_resume_and_returns_to_regular(monkeypatch):
    backend = backend_for([says('I hear you.'), says('Task reply'), says('Take your time.')])
    monkeypatch.setattr('vegapunk.cli.create_backend', lambda _: backend)
    main(prompter=ScriptedPrompter(['/journal', 'A quiet afternoon', '/save entry', '/new',
                                  'Regular task', '/sessions entry', 'More thoughts', '/exit']))
    requests = backend.provider.requests
    assert 'journal' in requests[0].system.lower()
    assert config.system_prompt in requests[1].system
    assert 'journal' in requests[2].system.lower()
    assert session_store.load_session_mode('entry') == 'journal'


def test_model_and_approval_switches_keep_journal_prompt(monkeypatch):
    before = backend_for(says('A brief acknowledgment.'))
    after = backend_for(says('Another brief acknowledgment.'), model_label='gpt-5.4')
    monkeypatch.setattr('vegapunk.cli.create_backend', lambda _: before)
    monkeypatch.setattr('vegapunk.commands.create_backend', lambda *args: after)

    class SwitchingPrompter:
        def __init__(self, *, status, toggle_approval):
            self.toggle = toggle_approval
            self.lines = iter(['/journal', 'First thought', '/model codex gpt-5.4',
                               'Second thought', '/exit'])

        def prompt(self):
            line = next(self.lines)
            if line == 'Second thought':
                assert self.toggle() == 'auto'
            return line

    monkeypatch.setattr('vegapunk.cli.PromptToolkitPrompter', SwitchingPrompter)
    main()
    assert 'journal' in before.provider.last_request.system.lower()
    assert 'Approval mode: manual' in before.provider.last_request.system
    assert 'journal' in after.provider.last_request.system.lower()
    assert 'Approval mode: auto' in after.provider.last_request.system
    assert config.system_prompt not in after.provider.last_request.system


@pytest.mark.parametrize('new_name', ['entry', 'renamed'])
def test_store_rename_without_mode_override_preserves_journal(new_name):
    session_store.save_session('entry', [user_turn('Thought')], conversation_mode='journal')
    session_store.rename_session('entry', new_name, [user_turn('Thought')])
    assert session_store.load_session_mode(new_name) == 'journal'


def test_journal_entries_remain_eligible_for_background_memory_processing():
    from vegapunk import memory_jobs

    session_store.save_session('entry', [user_turn('I prefer concise replies')], conversation_mode='journal')
    db.execute("UPDATE sessions SET updated_at='2020-01-01T00:00:00Z' WHERE slug='entry'")
    observed = []

    def extract(sources):
        observed.extend(sources)
        return []

    assert memory_jobs.process_one(extract, now='2030-01-01T00:00:00.000000Z')
    assert [source.text for source in observed] == ['I prefer concise replies']


def test_injected_session_journals_and_restores_its_custom_regular_prompt():
    session = session_for([says('Hello'), says('Listening'), says('Back to tasks'), says('Listening again')],
                          system_prompt='CUSTOM REGULAR PROMPT')
    provider = session.backend.provider
    main(prompter=ScriptedPrompter(['Hello', '/journal', 'My thoughts', '/save entry', '/new',
                                  'A task', '/sessions entry', 'More thoughts', '/exit']), session=session)
    assert provider.requests[0].system == 'CUSTOM REGULAR PROMPT'
    assert 'journal' in provider.requests[1].system.lower()
    assert provider.requests[2].system == 'CUSTOM REGULAR PROMPT'
    assert 'journal' in provider.requests[3].system.lower()

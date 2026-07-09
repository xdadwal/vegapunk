"""Tests for the one-time flat-file → database migration.

Isolation via the autouse conftest fixture (db.db_path → tmp; migrate legacy
seams → nonexistent). Each test that exercises a real import re-points the legacy
seams at a seeded fixture directory.
"""

from __future__ import annotations

import json
from datetime import date

import pytest
from prompt_toolkit.history import FileHistory

from vegapunk import db, migrate
from vegapunk.db_history import DbHistory
from vegapunk.memory import list_memory
from vegapunk.session_store import list_sessions, load_session


def _seed_legacy(tmp_path, monkeypatch):
    legacy = tmp_path / "legacy"
    sessions = legacy / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "chat-one.json").write_text(
        json.dumps(
            [
                {"role": "system", "content": "SYS"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "yo"},
            ]
        ),
        encoding="utf-8",
    )
    (sessions / "chat-two.json").write_text(
        json.dumps([{"role": "user", "content": "q"}]), encoding="utf-8"
    )
    (sessions / "broken.json").write_text("{not json", encoding="utf-8")  # corrupt

    memory_file = legacy / "memory.md"
    memory_file.write_text(
        "- [2024-01-02] likes ruff\n- [2024-01-03] uses zsh\na freeform note\n",
        encoding="utf-8",
    )

    history_file = legacy / "history"
    fh = FileHistory(str(history_file))  # write in prompt_toolkit's own format
    fh.store_string("first")
    fh.store_string("second")
    fh.store_string("line-a\nline-b")  # multi-line entry

    monkeypatch.setattr("vegapunk.migrate.legacy_sessions_dir", lambda: sessions)
    monkeypatch.setattr("vegapunk.migrate.legacy_memory_path", lambda: memory_file)
    monkeypatch.setattr("vegapunk.migrate.legacy_history_path", lambda: history_file)


def test_migrate_imports_all_three_stores(tmp_path, monkeypatch):
    _seed_legacy(tmp_path, monkeypatch)

    migrate.migrate_if_needed()

    # Sessions: both good ones imported (corrupt skipped), turn counts correct.
    assert dict(list_sessions()) == {"chat-one": 1, "chat-two": 1}
    assert load_session("chat-one")[1]["content"] == "hi"

    # Memory: dated bullets keep their order and dates; freeform line preserved.
    by_content = {m.content: m.created_at[:10] for m in list_memory()}
    assert by_content["likes ruff"] == "2024-01-02"
    assert by_content["uses zsh"] == "2024-01-03"
    assert by_content["a freeform note"] == date.today().isoformat()  # freeform -> today
    contents = [m.content for m in list_memory()]
    assert contents[:2] == ["likes ruff", "uses zsh"]  # historical order kept
    assert contents[-1] == "a freeform note"  # today's date sorts last

    # History: chronological id, so a fresh load returns newest-first.
    assert list(DbHistory().load_history_strings()) == ["line-a\nline-b", "second", "first"]


def test_migrate_skips_corrupt_session_with_note(tmp_path, monkeypatch, capsys):
    _seed_legacy(tmp_path, monkeypatch)
    migrate.migrate_if_needed()
    assert "broken.json" in capsys.readouterr().err


def test_migrate_skips_scalar_session_json_without_crashing(tmp_path, monkeypatch, capsys):
    # A session file whose top-level JSON is a scalar must be skipped, not crash
    # startup (would otherwise raise TypeError on the turn count).
    sessions = tmp_path / "legacy" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "good.json").write_text(json.dumps([{"role": "user", "content": "hi"}]), encoding="utf-8")
    (sessions / "scalar.json").write_text("null", encoding="utf-8")
    monkeypatch.setattr("vegapunk.migrate.legacy_sessions_dir", lambda: sessions)

    migrate.migrate_if_needed()  # must not raise

    assert dict(list_sessions()) == {"good": 1}  # scalar skipped, good imported
    assert "scalar.json" in capsys.readouterr().err


def test_migrate_preserves_order_within_a_shared_date(tmp_path, monkeypatch):
    memory_file = tmp_path / "legacy" / "memory.md"
    memory_file.parent.mkdir(parents=True)
    memory_file.write_text(
        "- [2024-05-01] first\n- [2024-05-01] second\n- [2024-05-01] third\n", encoding="utf-8"
    )
    monkeypatch.setattr("vegapunk.migrate.legacy_memory_path", lambda: memory_file)

    migrate.migrate_if_needed()

    # Same date on every bullet: the file-order microsecond index must keep them ordered.
    assert [m.content for m in list_memory()] == ["first", "second", "third"]


def test_migrate_db_failure_rolls_back_everything_and_retries(tmp_path, monkeypatch):
    _seed_legacy(tmp_path, monkeypatch)
    real_import_memory = migrate._import_memory

    def _boom(conn):
        raise db.StoreError("database died mid-migration")

    monkeypatch.setattr("vegapunk.migrate._import_memory", _boom)

    migrate.migrate_if_needed()  # must not raise

    # The whole transaction rolled back: no flag, and the sessions imported
    # earlier in the same transaction are gone too (nothing partially committed).
    assert db.query("SELECT value FROM meta WHERE key = 'flatfile_migrated'") == []
    assert list_sessions() == []

    # Retry with the store healthy completes cleanly — no duplicates anywhere.
    monkeypatch.setattr("vegapunk.migrate._import_memory", real_import_memory)
    migrate.migrate_if_needed()
    assert db.query("SELECT value FROM meta WHERE key = 'flatfile_migrated'") != []
    assert dict(list_sessions()) == {"chat-one": 1, "chat-two": 1}
    assert "likes ruff" in [m.content for m in list_memory()]
    assert list(DbHistory().load_history_strings()) == ["line-a\nline-b", "second", "first"]


def test_migrate_is_idempotent(tmp_path, monkeypatch):
    _seed_legacy(tmp_path, monkeypatch)
    migrate.migrate_if_needed()
    migrate.migrate_if_needed()  # second run must be a no-op (flag set)

    assert dict(list_sessions()) == {"chat-one": 1, "chat-two": 1}  # no duplicates
    assert len(list_memory()) == 3


def test_migrate_no_legacy_files_sets_flag_quietly(tmp_path, monkeypatch, capsys):
    # conftest already points the seams at nonexistent paths; be explicit.
    missing = tmp_path / "nowhere"
    monkeypatch.setattr("vegapunk.migrate.legacy_sessions_dir", lambda: missing / "sessions")
    monkeypatch.setattr("vegapunk.migrate.legacy_memory_path", lambda: missing / "memory.md")
    monkeypatch.setattr("vegapunk.migrate.legacy_history_path", lambda: missing / "history")

    migrate.migrate_if_needed()

    assert db.query("SELECT value FROM meta WHERE key = 'flatfile_migrated'")  # flag recorded
    assert capsys.readouterr().out == ""  # nothing imported -> no summary line

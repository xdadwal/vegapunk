"""Tests for DbHistory — REPL input history stored in the database.

Isolation via the autouse conftest fixture (db.db_path → tmp).
"""

from __future__ import annotations

from vegapunk import db
from vegapunk.db_history import DbHistory


def test_round_trip_newest_first():
    h = DbHistory()
    h.store_string("first")
    h.store_string("second")
    # A fresh instance reads from the database, newest first.
    assert list(DbHistory().load_history_strings()) == ["second", "first"]


def test_multiline_entry_intact():
    DbHistory().store_string("line1\nline2")
    assert list(DbHistory().load_history_strings()) == ["line1\nline2"]


def test_load_degrades_when_db_unavailable(monkeypatch, capsys):
    def _boom(*args, **kwargs):
        raise db.StoreError("db gone")

    monkeypatch.setattr("vegapunk.db.query", _boom)
    assert list(DbHistory().load_history_strings()) == []  # yields nothing, no raise
    assert "could not load" in capsys.readouterr().err


def test_store_degrades_when_db_unavailable(monkeypatch, capsys):
    def _boom(*args, **kwargs):
        raise db.StoreError("db gone")

    monkeypatch.setattr("vegapunk.db.execute", _boom)
    DbHistory().store_string("dropped")  # must not raise — losing one entry is OK
    assert "could not save entry" in capsys.readouterr().err

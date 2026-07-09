"""Tests for the session store — DB persistence of conversations.

Isolation comes from the autouse ``_isolated_vegapunk_home`` fixture (conftest),
which points ``db.db_path`` at a per-test tmp file.
"""

from __future__ import annotations

import pytest

from vegapunk import db
from vegapunk.session_store import (
    SessionNotFound,
    delete_session,
    exists,
    list_sessions,
    load_session,
    save_session,
    slugify,
    unique_name,
)


def test_slugify_basic():
    assert slugify("Fixing the Loop!") == "fixing-the-loop"


def test_slugify_is_traversal_safe():
    out = slugify("../../etc/passwd")
    assert "/" not in out and ".." not in out
    assert out == "etc-passwd"  # only [a-z0-9-] survive


def test_slugify_empty_when_nothing_usable():
    assert slugify("   !!!   ") == ""


def test_slugify_caps_length():
    assert len(slugify("word " * 50)) <= 40


def test_save_load_round_trip():
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]
    save_session("demo", msgs)
    assert load_session("demo") == msgs


def test_save_session_overwrites_in_place():
    save_session("demo", [{"role": "user", "content": "one"}])
    save_session("demo", [{"role": "user", "content": "two"}])
    assert load_session("demo") == [{"role": "user", "content": "two"}]
    assert list_sessions() == [("demo", 1)]  # still one row


def test_load_missing_raises():
    with pytest.raises(SessionNotFound):
        load_session("nope")


def test_unique_name_disambiguates():
    save_session("demo", [])
    assert unique_name("demo") == "demo-2"
    save_session("demo-2", [])
    assert unique_name("demo") == "demo-3"


def test_unique_name_free_when_unused():
    assert unique_name("fresh") == "fresh"


def test_exists_reflects_saved_sessions():
    assert exists("ghost") is False
    save_session("ghost", [])
    assert exists("ghost") is True


def test_delete_session_is_idempotent():
    save_session("gone", [])
    delete_session("gone")
    delete_session("gone")  # no row — no error the second time
    with pytest.raises(SessionNotFound):
        load_session("gone")


def test_list_sessions_counts_user_turns():
    save_session(
        "a",
        [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
            {"role": "user", "content": "z"},
        ],
    )
    assert dict(list_sessions())["a"] == 2  # two user turns


def test_list_sessions_degrades_when_db_unavailable(monkeypatch, capsys):
    def _boom(*args, **kwargs):
        raise db.StoreError("db is gone")

    monkeypatch.setattr("vegapunk.db.query", _boom)
    assert list_sessions() == []  # degrades instead of crashing the listing
    assert "could not list" in capsys.readouterr().err


def test_load_session_wraps_corrupt_blob_as_store_error():
    save_session("bad", [])
    # Corrupt the stored JSON directly, then load.
    db.execute("UPDATE sessions SET messages = '{not json' WHERE slug = 'bad'")
    with pytest.raises(db.StoreError, match="corrupt"):
        load_session("bad")

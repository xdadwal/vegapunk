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
    rows = list_sessions()
    assert len(rows) == 1  # still one row
    assert rows[0][0] == "demo" and rows[0][1] == 1


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
    rows = list_sessions()
    assert rows[0][0] == "a" and rows[0][1] == 2  # two user turns


def test_list_sessions_orders_by_recency_with_dates_and_limit():
    # Insert with controlled updated_at so ordering is deterministic.
    stamps = {
        "oldest": "2026-01-01T00:00:00.000000Z",
        "middle": "2026-02-01T00:00:00.000000Z",
        "newest": "2026-03-01T00:00:00.000000Z",
    }
    for slug, ts in stamps.items():
        db.execute(
            "INSERT INTO sessions (slug, messages, turns, created_at, updated_at) VALUES (?,?,?,?,?)",
            (slug, "[]", 0, ts, ts),
        )

    rows = list_sessions()
    assert [name for name, _turns, _date in rows] == ["newest", "middle", "oldest"]  # descending
    assert rows[0][2][:10] == "2026-03-01"  # date carried through

    # Limit returns only the most recent N.
    assert [name for name, _turns, _date in list_sessions(limit=2)] == ["newest", "middle"]


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

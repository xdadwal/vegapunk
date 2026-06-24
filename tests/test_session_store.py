"""Tests for the session store — disk persistence of conversations.

The sessions dir is read live via ``session_store.sessions_dir()``; ``config`` is
frozen, so we monkeypatch that helper to a tmp dir (the same trick the memory and
filesystem tests use).
"""

from __future__ import annotations

import pytest

from vegapunk.session_store import (
    SessionNotFound,
    delete_session,
    list_sessions,
    load_session,
    save_session,
    slugify,
    unique_name,
)


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr("vegapunk.session_store.sessions_dir", lambda: tmp_path)
    return tmp_path


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


def test_save_load_round_trip(store):
    msgs = [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "yo"},
    ]
    save_session("demo", msgs)
    assert load_session("demo") == msgs


def test_load_missing_raises(store):
    with pytest.raises(SessionNotFound):
        load_session("nope")


def test_unique_name_disambiguates(store):
    save_session("demo", [])
    assert unique_name("demo") == "demo-2"
    save_session("demo-2", [])
    assert unique_name("demo") == "demo-3"


def test_unique_name_free_when_unused(store):
    assert unique_name("fresh") == "fresh"


def test_delete_session_is_idempotent(store):
    save_session("gone", [])
    delete_session("gone")
    delete_session("gone")  # missing_ok — no error second time
    with pytest.raises(SessionNotFound):
        load_session("gone")


def test_list_sessions_counts_user_turns_and_skips_corrupt(store):
    save_session(
        "a",
        [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
            {"role": "user", "content": "z"},
        ],
    )
    (store / "bad.json").write_text("{not json", encoding="utf-8")  # corrupt file

    rows = dict(list_sessions())
    assert rows["a"] == 2  # two user turns
    assert "bad" not in rows  # corrupt file skipped, not crashed on

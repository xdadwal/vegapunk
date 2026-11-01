"""Stable session identity survives display-name changes and resumption."""

from vegapunk import session_store


def test_identity_survives_save_and_rename():
    session_store.save_session("original", [])
    identity = session_store.session_identity("original")
    session_store.save_session("original", [])
    session_store.rename_session("original", "renamed", [])
    assert session_store.session_identity("renamed") == identity


def test_distinct_sessions_have_distinct_identities():
    session_store.save_session("first", [])
    session_store.save_session("second", [])
    assert session_store.session_identity("first") != session_store.session_identity("second")

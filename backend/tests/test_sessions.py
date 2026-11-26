from datetime import UTC, datetime, timedelta

from vegapunk.auth.sessions import (
    clear_failures,
    create_session,
    delete_all_sessions,
    delete_session,
    locked_until,
    record_failure,
    session_is_valid,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def test_session_round_trip_and_expiry(conn):
    token = create_session(conn, T0)
    assert session_is_valid(conn, token, T0 + timedelta(days=29))
    assert not session_is_valid(conn, token, T0 + timedelta(days=31))
    assert not session_is_valid(conn, "garbage", T0)


def test_token_is_not_stored_in_plain_text(conn):
    token = create_session(conn, T0)
    assert token not in [r[0] for r in conn.execute("SELECT token_hash FROM sessions")]


def test_deleted_session_is_invalid(conn):
    token = create_session(conn, T0)
    delete_session(conn, token)
    assert not session_is_valid(conn, token, T0)


def test_lockout_after_five_failures_then_release(conn):
    for i in range(4):
        record_failure(conn, T0 + timedelta(minutes=i))
    assert locked_until(conn, T0 + timedelta(minutes=4)) is None
    record_failure(conn, T0 + timedelta(minutes=4))
    assert locked_until(conn, T0 + timedelta(minutes=5)) == T0 + timedelta(minutes=15)
    assert locked_until(conn, T0 + timedelta(minutes=16)) is None


def test_clear_failures(conn):
    for _ in range(5):
        record_failure(conn, T0)
    clear_failures(conn)
    assert locked_until(conn, T0) is None


def test_delete_all_can_keep_one(conn):
    keep, drop = create_session(conn, T0), create_session(conn, T0)
    delete_all_sessions(conn, except_token=keep)
    assert session_is_valid(conn, keep, T0)
    assert not session_is_valid(conn, drop, T0)

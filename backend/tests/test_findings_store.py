from datetime import UTC, datetime, timedelta

import pytest

from vegapunk.watch.findings import (
    MAX_DIAGNOSIS,
    MAX_EVIDENCE_LINES,
    MAX_EVIDENCE_TEXT,
    MAX_FIX,
    MAX_TITLE,
    EvidenceLine,
    FindingDraft,
    get_finding,
    investigation_counts,
    list_findings,
    load_check_settings,
    open_count,
    open_keys,
    record_investigation,
    resolve,
    review,
    save_check_settings,
    upsert_finding,
    used_markers,
)

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def draft(subject="web", title="Web is down", evidence=None, **over):
    ev = (EvidenceLine("log", "boom", "2026-01-01T11:59:00Z"),) if evidence is None else evidence
    args = {"subject": subject, "title": title, "diagnosis": "d", "suggested_fix": "f"}
    args.update(over)
    return FindingDraft(evidence=ev, **args)


def up(conn, d=None, key="crash:web", now=NOW, trigger="watch"):
    return upsert_finding(conn, d or draft(), key=key, trigger=trigger, now=now)


def test_upsert_twice_keeps_one_row_and_replaces_evidence(conn):
    a = up(conn)
    b = up(
        conn,
        draft(evidence=(EvidenceLine("metric", "cpu 99%", None),)),
        now=NOW + timedelta(hours=1),
    )
    assert a == b
    assert conn.execute("SELECT COUNT(*) FROM findings").fetchone()[0] == 1
    f = get_finding(conn, a)
    assert f["evidence"] == [{"source": "metric", "text": "cpu 99%", "at": None}]
    assert f["first_seen_at"] == "2026-01-01T12:00:00Z"
    assert f["last_seen_at"] == "2026-01-01T13:00:00Z"
    assert f["key"] == "crash:web" and f["status"] == "open" and f["trigger"] == "watch"


def test_reviewed_finding_is_not_reopened_a_new_one_is_created(conn):
    a = up(conn)
    assert review(conn, a, NOW) is True
    b = up(conn, now=NOW + timedelta(hours=1))
    assert a != b
    assert get_finding(conn, a)["status"] == "reviewed"
    assert get_finding(conn, b)["status"] == "open"
    assert open_keys(conn) == {"crash:web"}


def test_long_fields_and_evidence_are_cut(conn):
    ev = tuple(EvidenceLine("log", "x" * 1000, None) for _ in range(50))
    d = draft(title="t" * 500, diagnosis="d" * 5000, suggested_fix="f" * 5000, evidence=ev)
    f = get_finding(conn, up(conn, d))
    assert len(f["title"]) == MAX_TITLE == 120
    assert len(f["diagnosis"]) == MAX_DIAGNOSIS == 1200
    assert len(f["suggested_fix"]) == MAX_FIX == 600
    assert len(f["evidence"]) == MAX_EVIDENCE_LINES == 20
    assert all(len(e["text"]) == MAX_EVIDENCE_TEXT == 300 for e in f["evidence"])


def test_review_unknown_id_is_false_and_reviewed_twice_is_false(conn):
    assert review(conn, 999, NOW) is False
    a = up(conn)
    assert review(conn, a, NOW) is True
    assert review(conn, a, NOW) is False


def test_get_unknown_is_none(conn):
    assert get_finding(conn, 1) is None


def test_resolve_marks_open_finding_resolved(conn):
    a = up(conn)
    resolve(conn, "crash:web", NOW + timedelta(hours=2))
    f = get_finding(conn, a)
    assert f["status"] == "resolved" and f["resolved_at"] == "2026-01-01T14:00:00Z"
    assert open_keys(conn) == set() and open_count(conn) == 0


def test_list_filters_and_orders_open_first_newest_first(conn):
    old = up(conn, draft(subject="db", title="Disk full"), key="disk:db", now=NOW)
    new = up(
        conn, draft(subject="web", title="Web down"), key="crash:web", now=NOW + timedelta(hours=1)
    )
    done = up(
        conn, draft(subject="cache", title="Cache"), key="x:cache", now=NOW + timedelta(hours=2)
    )
    resolve(conn, "x:cache", NOW)
    assert [f["id"] for f in list_findings(conn, status=None, query=None)] == [new, old, done]
    assert [f["id"] for f in list_findings(conn, status="resolved", query=None)] == [done]
    assert [f["id"] for f in list_findings(conn, status=None, query="DISK")] == [old]
    assert [f["id"] for f in list_findings(conn, status=None, query="WEB")] == [new]
    assert "evidence" not in list_findings(conn, status=None, query=None)[0]


def test_open_count_counts_only_open(conn):
    a = up(conn)
    up(conn, key="b:web")
    up(conn, key="c:web")
    review(conn, a, NOW)
    resolve(conn, "c:web", NOW)
    assert open_count(conn) == 1


def test_check_settings_default_and_save(conn):
    s = load_check_settings(conn)
    assert s.enabled is True and s.hour == 2
    save_check_settings(conn, False, 23)
    s = load_check_settings(conn)
    assert s.enabled is False and s.hour == 23
    for bad in (-1, 24):
        with pytest.raises(ValueError):
            save_check_settings(conn, True, bad)


def test_migrate_creates_tables_and_default_row(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "findings",
        "finding_evidence",
        "check_settings",
        "investigations",
        "container_restarts",
    } <= names
    assert conn.execute("SELECT id, enabled, hour FROM check_settings").fetchall()[0][:] == (
        1,
        1,
        2,
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(containers)")}
    assert "oom_killed" in cols


def test_deleting_a_finding_deletes_evidence(conn):
    a = up(conn)
    assert conn.execute("SELECT COUNT(*) FROM finding_evidence").fetchone()[0] == 1
    conn.execute("DELETE FROM findings WHERE id = ?", (a,))
    assert conn.execute("SELECT COUNT(*) FROM finding_evidence").fetchone()[0] == 0


def test_investigation_counts_every_outcome_in_24h(conn):
    assert investigation_counts(conn, "k", NOW) == (None, None, 0)
    record_investigation(conn, "k", "watch", NOW - timedelta(hours=30), "failed", "old")
    record_investigation(conn, "k", "watch", NOW - timedelta(hours=5), "nothing")
    record_investigation(conn, "k", "watch", NOW - timedelta(hours=3), "failed", "err")
    record_investigation(conn, "k", "watch", NOW - timedelta(hours=1), "finding")
    record_investigation(conn, "other", "watch", NOW, "finding")
    last, failed, count = investigation_counts(conn, "k", NOW)
    assert last == NOW - timedelta(hours=1)
    assert failed == NOW - timedelta(hours=3)
    assert count == 3


def test_used_markers(conn):
    record_investigation(conn, "k", "watch", NOW, "finding", marker="m1")
    record_investigation(conn, "k", "watch", NOW, "nothing", marker="m2")
    record_investigation(conn, "k", "watch", NOW, "nothing")
    record_investigation(conn, "o", "watch", NOW, "nothing", marker="m3")
    assert used_markers(conn, "k") == {"m1", "m2"}

from datetime import UTC, datetime, timedelta

import pytest

from vegapunk.capture.ingest import ingest
from vegapunk.capture.records import (
    ContainerList,
    ContainerObservation,
    ContainerSample,
    HostFacts,
    HostSample,
)

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
FACTS = HostFacts("nas", "TrueNAS", "6.1", "29.0", 8, 16_000)


def host_sample(cpu=10.0, boot=T0 - timedelta(days=3)):
    return HostSample(cpu, 4_000, 16_000, 500, 1_000, boot)


def obs(cid, name, state="running", health=None, restarts=0):
    return ContainerObservation(cid, name, "img:1", state, health, restarts, T0)


def rows(conn, table):
    return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3")]


def test_host_records_are_stored_and_updated(conn):
    ingest(conn, T0, [FACTS, host_sample()])
    host = conn.execute("SELECT * FROM host").fetchone()
    assert (host["hostname"], host["cpu_count"], host["mem_total"]) == ("nas", 8, 16_000)
    assert host["boot_time"] == (T0 - timedelta(days=3)).isoformat()
    sample = conn.execute("SELECT * FROM host_metrics").fetchone()
    assert (sample["res"], sample["ts"], sample["cpu_pct"]) == (0, int(T0.timestamp()), 10.0)
    newer_boot = T0 + timedelta(minutes=5)
    ingest(conn, T0 + timedelta(seconds=15), [host_sample(boot=newer_boot)])
    host = conn.execute("SELECT * FROM host").fetchone()
    assert host["hostname"] == "nas"  # facts untouched by a sample
    assert host["boot_time"] == newer_boot.isoformat()


def test_container_list_upserts_and_keeps_first_seen(conn):
    ingest(conn, T0, [ContainerList((obs("a", "web"),))])
    later = T0 + timedelta(minutes=1)
    ingest(conn, later, [ContainerList((obs("a", "web", state="exited", restarts=2),))])
    row = conn.execute("SELECT * FROM containers WHERE id = 'a'").fetchone()
    assert row["state"] == "exited" and row["restart_count"] == 2
    assert row["first_seen_at"] == T0.isoformat()
    assert row["last_seen_at"] == later.isoformat()


def test_container_missing_from_a_later_list_is_marked_removed(conn):
    ingest(conn, T0, [ContainerList((obs("a", "web"), obs("b", "db")))])
    later = T0 + timedelta(minutes=1)
    ingest(conn, later, [ContainerList((obs("a", "web"),))])
    removed = {
        r["id"]: r["removed_at"] for r in conn.execute("SELECT id, removed_at FROM containers")
    }
    assert removed == {"a": None, "b": later.isoformat()}


def test_an_empty_listing_removes_everything(conn):
    ingest(conn, T0, [ContainerList((obs("a", "web"),))])
    ingest(conn, T0 + timedelta(minutes=1), [ContainerList(())])
    assert conn.execute("SELECT removed_at FROM containers").fetchone()[0] is not None


def test_container_that_returns_is_no_longer_removed(conn):
    ingest(conn, T0, [ContainerList((obs("a", "web"),))])
    ingest(conn, T0 + timedelta(minutes=1), [ContainerList(())])
    ingest(conn, T0 + timedelta(minutes=2), [ContainerList((obs("a", "web"),))])
    assert conn.execute("SELECT removed_at FROM containers").fetchone()[0] is None


def test_samples_are_stored_raw(conn):
    ingest(conn, T0, [ContainerList((obs("a", "web"),)), ContainerSample("a", 5.0, 100, 1_000)])
    row = conn.execute("SELECT * FROM container_metrics").fetchone()
    assert tuple(row) == ("a", 0, int(T0.timestamp()), 5.0, 100, 1_000)


def test_a_sample_listed_before_its_container_is_still_stored(conn):
    ingest(conn, T0, [ContainerSample("a", 5.0, 100, None), ContainerList((obs("a", "web"),))])
    assert conn.execute("SELECT count(*) FROM container_metrics").fetchone()[0] == 1


def test_ingesting_the_same_records_twice_changes_nothing(conn):
    records = [
        FACTS,
        host_sample(),
        ContainerList((obs("a", "web"),)),
        ContainerSample("a", 5.0, 1, 2),
    ]
    ingest(conn, T0, records)
    before = {t: rows(conn, t) for t in ("host", "host_metrics", "containers", "container_metrics")}
    ingest(conn, T0, records)
    assert {t: rows(conn, t) for t in before} == before


def test_a_sample_for_an_unknown_container_is_ignored(conn):
    ingest(conn, T0, [ContainerSample("ghost", 1.0, 1, 1)])
    assert conn.execute("SELECT count(*) FROM container_metrics").fetchone()[0] == 0


def test_null_metrics_are_stored_as_null(conn):
    ingest(conn, T0, [ContainerList((obs("a", "web"),)), ContainerSample("a", None, None, None)])
    row = conn.execute("SELECT * FROM container_metrics").fetchone()
    assert (row["cpu_pct"], row["mem_used"], row["mem_limit"]) == (None, None, None)


def test_failed_ingest_stores_nothing(conn):
    with pytest.raises(TypeError):
        ingest(conn, T0, [host_sample(), ContainerList((obs("a", "web"),)), "not a record"])  # type: ignore[list-item]
    assert conn.execute("SELECT count(*) FROM host_metrics").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM containers").fetchone()[0] == 0


def test_no_container_list_means_no_removals(conn):
    ingest(conn, T0, [ContainerList((obs("a", "web"),))])
    ingest(conn, T0 + timedelta(minutes=1), [host_sample(), ContainerSample("a", 1.0, 1, 1)])
    assert conn.execute("SELECT removed_at FROM containers").fetchone()[0] is None


def test_two_containers_with_one_name_are_both_stored(conn):
    ingest(conn, T0, [ContainerList((obs("old", "web", state="exited"),))])
    ingest(conn, T0 + timedelta(minutes=1), [ContainerList((obs("new", "web"),))])
    states = {
        r["id"]: r["removed_at"] is None
        for r in conn.execute("SELECT id, removed_at FROM containers")
    }
    assert states == {"old": False, "new": True}

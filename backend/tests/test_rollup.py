from datetime import UTC, datetime, timedelta

from vegapunk.capture.collectors import SOURCES  # noqa: F401  (keeps import side effects honest)
from vegapunk.capture.ingest import ingest
from vegapunk.capture.records import (
    ContainerList,
    ContainerObservation,
    ContainerSample,
    HostSample,
)
from vegapunk.capture.rollup import BUCKET, RAW_KEEP, ROLLUP_KEEP, prune, rollup
from vegapunk.capture.scheduler import Scheduler

T = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)  # on a 5-minute boundary


def ts(t):
    return int(t.timestamp())


def host(conn, t, cpu, mem=100):
    ingest(conn, t, [HostSample(cpu, mem, 1_000, 10, 100, T)])


def container(conn, t, cpu, mem=100, cid="a"):
    obs = ContainerObservation(cid, cid, "img", "running", None, 0, t)
    ingest(conn, t, [ContainerList((obs,)), ContainerSample(cid, cpu, mem, None)])


def host_rows(conn, res):
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT ts, cpu_pct, mem_used FROM host_metrics WHERE res = ? ORDER BY ts", (res,)
        )
    ]


def test_bucket_constants():
    assert (BUCKET, RAW_KEEP, ROLLUP_KEEP) == (300, 86_400, 30 * 86_400)


def test_rollup_averages_raw_samples_into_a_bucket(conn):
    for i, (cpu, mem) in enumerate([(10, 100), (20, 200), (30, 300)]):
        host(conn, T + timedelta(seconds=15 * i), cpu, mem)
    rollup(conn, T + timedelta(minutes=1))
    assert host_rows(conn, 300) == [(ts(T), 20.0, 200)]


def test_rollup_is_idempotent(conn):
    host(conn, T, 10)
    host(conn, T + timedelta(seconds=15), 30)
    rollup(conn, T + timedelta(minutes=1))
    first = host_rows(conn, 300)
    rollup(conn, T + timedelta(minutes=1))
    assert host_rows(conn, 300) == first


def test_rollup_updates_the_current_bucket_as_samples_arrive(conn):
    host(conn, T, 10)
    rollup(conn, T + timedelta(seconds=30))
    assert host_rows(conn, 300) == [(ts(T), 10.0, 100)]
    host(conn, T + timedelta(minutes=2), 30)
    rollup(conn, T + timedelta(minutes=2, seconds=15))
    assert host_rows(conn, 300) == [(ts(T), 20.0, 100)]


def test_rollup_ignores_nulls_and_keeps_all_null_columns_null(conn):
    host(conn, T, None)
    host(conn, T + timedelta(seconds=15), 40)
    host(conn, T + timedelta(minutes=5), None)
    rollup(conn, T + timedelta(minutes=6))
    assert host_rows(conn, 300) == [(ts(T), 40.0, 100), (ts(T) + 300, None, 100)]


def test_rollup_covers_container_metrics_per_container(conn):
    container(conn, T, 10, cid="a")
    container(conn, T + timedelta(seconds=15), 30, cid="a")
    container(conn, T, 50, cid="b")
    rollup(conn, T + timedelta(minutes=1))
    rows = {
        r["container_id"]: r["cpu_pct"]
        for r in conn.execute("SELECT container_id, cpu_pct FROM container_metrics WHERE res = 300")
    }
    assert rows == {"a": 20.0, "b": 50.0}


def test_empty_buckets_write_nothing(conn):
    rollup(conn, T + timedelta(minutes=1))
    assert host_rows(conn, 300) == []
    host(conn, T, 10)
    host(conn, T + timedelta(minutes=10), 10)
    rollup(conn, T + timedelta(minutes=10, seconds=30))
    assert [r[0] for r in host_rows(conn, 300)] == [ts(T), ts(T) + 600]


def test_rollup_only_looks_at_recent_buckets(conn):
    host(conn, T, 10)
    rollup(conn, T + timedelta(hours=1))
    assert host_rows(conn, 300) == []


def test_prune_removes_old_raw_and_keeps_recent(conn):
    now = T + timedelta(days=5)
    host(conn, now - timedelta(hours=25), 1)
    host(conn, now - timedelta(hours=23), 2)
    container(conn, now - timedelta(hours=25), 1)
    container(conn, now - timedelta(hours=23), 2)
    prune(conn, now)
    assert [r[1] for r in host_rows(conn, 0)] == [2.0]
    assert conn.execute("SELECT count(*) FROM container_metrics WHERE res = 0").fetchone()[0] == 1


def test_prune_removes_old_rollups_and_keeps_recent(conn):
    now = T + timedelta(days=60)
    for age in (31, 29):
        conn.execute(
            "INSERT INTO host_metrics (res, ts, cpu_pct) VALUES (300, ?, ?)",
            (ts(now - timedelta(days=age)), float(age)),
        )
    prune(conn, now)
    assert [r[1] for r in host_rows(conn, 300)] == [29.0]


def test_prune_after_days_of_downtime_keeps_what_the_rules_say(conn):
    for i in range(4):
        host(conn, T + timedelta(seconds=15 * i), 10)
    rollup(conn, T + timedelta(minutes=2))
    container(conn, T, 10)
    prune(conn, T + timedelta(days=3))
    assert host_rows(conn, 0) == []  # raw is only kept for a day
    assert len(host_rows(conn, 300)) == 1  # the roll-up survives for 30 days
    assert (
        conn.execute("SELECT count(*) FROM containers WHERE removed_at IS NULL").fetchone()[0] == 1
    )


def test_prune_never_touches_the_host_row_or_live_containers(conn):
    host(conn, T, 10)
    container(conn, T, 10)
    prune(conn, T + timedelta(days=400))
    assert conn.execute("SELECT count(*) FROM host").fetchone()[0] == 1
    assert conn.execute("SELECT count(*) FROM containers").fetchone()[0] == 1


def test_prune_deletes_long_removed_containers_with_their_metrics(conn):
    container(conn, T, 10, cid="gone")
    ingest(conn, T + timedelta(minutes=1), [ContainerList(())])  # marks it removed
    conn.execute("UPDATE container_metrics SET res = 300")  # pretend it was rolled up
    prune(conn, T + timedelta(days=29))
    assert conn.execute("SELECT count(*) FROM containers").fetchone()[0] == 1
    prune(conn, T + timedelta(days=32))
    assert conn.execute("SELECT count(*) FROM containers").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM container_metrics").fetchone()[0] == 0


# ---- scheduler wiring
class HostOnly:
    name = "host"

    def __init__(self, fail=False):
        self.fail = fail

    def collect(self, now):
        if self.fail:
            raise RuntimeError("nope")
        return [HostSample(10.0, 1, 2, 3, 4, T)]


def test_scheduler_rolls_up_once_per_cycle_and_prunes_hourly(db_path):
    from vegapunk.db import connect

    clock = {"t": T}
    s = Scheduler(db_path, [HostOnly()], interval=15, clock=lambda: clock["t"])

    def count(sql):
        c = connect(db_path)
        try:
            return c.execute(sql).fetchone()[0]
        finally:
            c.close()

    old_raw = "SELECT count(*) FROM host_metrics WHERE res = 0 AND cpu_pct = 1"

    def plant_old_raw(age):
        c = connect(db_path)
        c.execute(
            "INSERT OR REPLACE INTO host_metrics (res, ts, cpu_pct) VALUES (0, ?, 1)",
            (ts(clock["t"] - age),),
        )
        c.close()

    plant_old_raw(timedelta(hours=30))
    s.run_once()
    assert count("SELECT count(*) FROM host_metrics WHERE res = 300") == 1  # rolled up
    assert count(old_raw) == 0  # the first cycle prunes

    clock["t"] = T + timedelta(minutes=30)
    plant_old_raw(timedelta(hours=30))
    s.run_once()
    assert count(old_raw) == 1  # not yet an hour later

    clock["t"] = T + timedelta(minutes=61)
    s.run_once()
    assert count(old_raw) == 0  # pruned again after an hour


def test_scheduler_does_not_roll_up_when_every_collector_failed(db_path):
    from vegapunk.db import connect

    Scheduler(db_path, [HostOnly(fail=True)], interval=15, clock=lambda: T).run_once()
    c = connect(db_path)
    assert c.execute("SELECT count(*) FROM host_metrics").fetchone()[0] == 0
    c.close()

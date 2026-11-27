from datetime import UTC, datetime, timedelta

from vegapunk.capture.ingest import ingest
from vegapunk.capture.records import ContainerList, ContainerObservation
from vegapunk.watch.findings import record_investigation
from vegapunk.watch.rules import Signal, active_oom_keys, evaluate

NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
T = int(NOW.timestamp())
MIB = 1024 * 1024


def container(
    conn, cid="c1", name="web", state="running", restarts=0, removed=False, oom=0, marker=None
):
    conn.execute(
        "INSERT INTO containers (id, name, image, state, restart_count, first_seen_at, "
        "last_seen_at, removed_at, oom_killed, exit_marker) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (cid, name, "img", state, restarts, "x", "x", "x" if removed else None, oom, marker),
    )
    conn.commit()


def host_rows(conn, values, *, start=600, step=15, res=0, field="cpu_pct"):
    """values: one per sample, oldest first, ending at now (or `start` seconds before)."""
    ts = T - start
    for v in values:
        conn.execute(f"INSERT INTO host_metrics (res, ts, {field}) VALUES (?,?,?)", (res, ts, v))
        ts += step
    conn.commit()


def full(value, n=41):
    return [value] * n


def mem_rows(conn, pcts, start=600, step=15):
    ts = T - start
    for p in pcts:
        used = None if p is None else int(p * 10)
        conn.execute(
            "INSERT INTO host_metrics (res, ts, mem_used, mem_total) VALUES (0,?,?,1000)",
            (ts, used),
        )
        ts += step
    conn.commit()


def cont_rows(conn, cid, pcts, limit=512 * MIB, start=600, step=15, cpu=None):
    ts = T - start
    for p in pcts:
        used = None if p is None else int(limit * p / 100) if limit else 100
        conn.execute(
            "INSERT INTO container_metrics (container_id, res, ts, cpu_pct, mem_used, mem_limit) "
            "VALUES (?,0,?,?,?,?)",
            (cid, ts, cpu, used, limit),
        )
        ts += step
    conn.commit()


def disk_row(conn, ts, used, total=100, res=0):
    conn.execute(
        "INSERT INTO host_metrics (res, ts, disk_used, disk_total) VALUES (?,?,?,?)",
        (res, ts, used, total),
    )


def keys(conn, **kw):
    return {s.key for s in evaluate(conn, NOW, **kw)}


# restart loop (fixtures go through the real ingest)


def see(conn, ago_min, count, cid="c1", name="web", state="running"):
    at = NOW - timedelta(minutes=ago_min)
    ingest(
        conn, at, [ContainerList((ContainerObservation(cid, name, "img", state, None, count, at),))]
    )


def test_three_single_restarts_in_the_window_fire(conn):
    for ago, count in [(14, 0), (11, 1), (7, 2), (3, 3)]:
        see(conn, ago, count)
    (s,) = evaluate(conn, NOW)
    assert s == Signal("restart_loop:web", "restart_loop", "web", "Restarted 3 times in 11 min.")


def test_two_restarts_do_nothing(conn):
    for ago, count in [(14, 0), (8, 1), (3, 2)]:
        see(conn, ago, count)
    assert keys(conn) == set()


def test_one_cycle_jumping_zero_to_three_fires(conn):
    see(conn, 10, 0)
    see(conn, 5, 3)
    assert keys(conn) == {"restart_loop:web"}


def test_restarts_spread_over_forty_minutes_do_not_fire(conn):
    for ago, count in [(41, 0), (40, 1), (25, 2), (10, 3)]:
        see(conn, ago, count)
    assert keys(conn) == set()


def test_a_count_going_down_contributes_nothing(conn):
    see(conn, 12, 10)
    see(conn, 8, 2)  # recreated with a lower count
    see(conn, 4, 4)  # +2
    assert keys(conn) == set()
    see(conn, 2, 5)  # +1 makes three
    assert keys(conn) == {"restart_loop:web"}


def test_removed_container_restart_loop_is_ignored(conn):
    see(conn, 10, 0)
    see(conn, 5, 3)
    ingest(conn, NOW - timedelta(minutes=1), [ContainerList(())])
    assert keys(conn) == set()


def two_with_one_name(conn):
    """An old container and its replacement, both in one listing (as Docker can report)."""
    at = NOW - timedelta(minutes=10)
    old = ContainerObservation("old", "web", "img", "running", None, 0, at)
    ingest(conn, at, [ContainerList((old,))])
    at = NOW - timedelta(minutes=2)
    new = ContainerObservation("new", "web", "img", "running", None, 0, at)
    ingest(conn, at, [ContainerList((old, new))])
    # the newer row has the later last_seen_at only if seen later; make old older explicitly
    conn.execute("UPDATE containers SET last_seen_at = '2026-01-01T11:00:00Z' WHERE id = 'old'")
    conn.commit()


def test_only_the_newest_row_for_a_name_is_used(conn):
    two_with_one_name(conn)
    conn.execute("UPDATE containers SET oom_killed = 1, exit_marker = 'm' WHERE id = 'old'")
    conn.commit()
    assert keys(conn) == set()
    conn.execute("UPDATE containers SET oom_killed = 1, exit_marker = 'm' WHERE id = 'new'")
    conn.commit()
    assert [s.key for s in evaluate(conn, NOW)] == ["oom_kill:web"]


def test_duplicate_names_give_unique_keys_for_memory(conn):
    two_with_one_name(conn)
    cont_rows(conn, "old", full(95))
    cont_rows(conn, "new", full(95))
    assert [s.key for s in evaluate(conn, NOW)] == ["high_memory:web"]


# out of memory


def test_oom_fires_once_per_marker(conn):
    container(conn, state="exited", oom=1, marker="m1")
    (s,) = evaluate(conn, NOW)
    assert (s.key, s.rule, s.marker) == ("oom_kill:web", "oom_kill", "m1")
    assert s.summary == "Killed for running out of memory."
    record_investigation(conn, "oom_kill:web", "watch", NOW, "finding", marker="m1")
    assert keys(conn) == set()
    conn.execute("UPDATE containers SET exit_marker = 'm2'")
    conn.commit()
    assert keys(conn) == {"oom_kill:web"}


def test_oom_needs_marker_and_flag_and_not_removed(conn):
    container(conn, "a", "a", oom=1, marker=None)
    container(conn, "b", "b", oom=0, marker="m")
    container(conn, "c", "c", oom=1, marker="m", removed=True)
    assert keys(conn) == set()


def test_active_oom_keys_hold_while_the_newest_row_is_oom_killed(conn):
    container(conn, "a", "a", state="exited", oom=1, marker="m1")
    container(conn, "b", "b", oom=0, marker="m")
    container(conn, "c", "c", oom=1, marker="m", removed=True)
    record_investigation(conn, "oom_kill:a", "watch", NOW, "finding", marker="m1")
    assert evaluate(conn, NOW) == []  # the marker is used: no new signal ...
    assert active_oom_keys(conn) == {"oom_kill:a"}  # ... but the kill still holds
    conn.execute("UPDATE containers SET oom_killed = 0 WHERE id = 'a'")
    conn.commit()
    assert active_oom_keys(conn) == set()


# sustained host cpu


def test_host_cpu_at_90_for_ten_minutes_fires(conn):
    host_rows(conn, full(90))
    (s,) = evaluate(conn, NOW)
    assert (s.key, s.rule, s.summary) == ("high_cpu:host", "high_cpu", "CPU at 90% for 10 min.")


def test_host_cpu_one_low_sample_does_not_fire(conn):
    host_rows(conn, [90] * 20 + [89.9] + [90] * 20)
    assert keys(conn) == set()


def test_host_cpu_under_ten_minutes_does_not_fire(conn):
    host_rows(conn, full(95, 21), start=300)
    assert keys(conn) == set()


def test_host_cpu_coverage_boundary(conn):
    # 32 of 40 expected samples is exactly 80%: spaced to keep gaps within 2x the interval
    host_rows(conn, full(95, 33), start=600, step=19)  # ends at T+26; keep inside window
    conn.execute("DELETE FROM host_metrics WHERE ts > ?", (T,))
    conn.commit()
    # 32 samples remain in the window with gaps of 19 s
    assert keys(conn) == {"high_cpu:host"}


def test_host_cpu_under_coverage_does_not_fire(conn):
    host_rows(conn, full(95, 31), start=600, step=19)
    conn.execute("DELETE FROM host_metrics WHERE ts > ?", (T,))
    conn.commit()
    # 31 samples is under 80% (and the tail is short)
    assert keys(conn) == set()


def test_host_cpu_gap_over_twice_interval_does_not_fire(conn):
    host_rows(conn, full(95, 21), start=600)  # up to T-300
    host_rows(conn, full(95, 21), start=300 - 31)  # restart after a 46 s gap
    assert keys(conn) == set()


def test_host_cpu_gap_of_exactly_twice_interval_fires(conn):
    host_rows(conn, full(95, 21), start=600)  # up to T-300
    host_rows(conn, full(95, 20), start=300 - 15)  # next at T-285+... see below
    # first batch ends T-300, second starts T-285: gap 15. Remove one to make a 30 s gap.
    conn.execute("DELETE FROM host_metrics WHERE ts = ?", (T - 285,))
    conn.commit()
    assert keys(conn) == {"high_cpu:host"}


def test_host_cpu_null_sample_breaks_run(conn):
    host_rows(conn, [95] * 20 + [None] + [95] * 20)
    assert keys(conn) == set()


def test_host_cpu_stale_samples_do_not_fire(conn):
    host_rows(conn, full(95), start=1200)
    assert keys(conn) == set()


# memory


def test_host_memory_fires(conn):
    mem_rows(conn, full(94))
    (s,) = evaluate(conn, NOW)
    assert (s.key, s.rule, s.summary) == (
        "high_memory:host",
        "high_memory",
        "Memory at 94% for 10 min.",
    )


def test_host_memory_below_does_not_fire(conn):
    mem_rows(conn, full(89))
    assert keys(conn) == set()


def test_container_memory_against_its_own_limit(conn):
    container(conn)
    cont_rows(conn, "c1", full(94))
    (s,) = evaluate(conn, NOW)
    assert (s.key, s.rule, s.subject) == ("high_memory:web", "high_memory", "web")
    assert s.summary == "Memory at 94% of its 512 MiB limit for 10 min."


def test_container_memory_below_90_does_not_fire(conn):
    container(conn)
    cont_rows(conn, "c1", [94] * 20 + [89] + [94] * 20)
    assert keys(conn) == set()


def test_container_without_limit_never_fires(conn):
    container(conn)
    cont_rows(conn, "c1", full(99), limit=None)
    assert keys(conn) == set()
    conn.execute("UPDATE container_metrics SET mem_limit = 0")
    conn.commit()
    assert keys(conn) == set()


def test_container_cpu_never_fires(conn):
    container(conn)
    cont_rows(conn, "c1", full(10), cpu=99.0)
    assert keys(conn) == set()


def test_stopped_or_removed_container_memory_ignored(conn):
    container(conn, "a", "a", state="exited")
    container(conn, "b", "b", removed=True)
    cont_rows(conn, "a", full(99))
    cont_rows(conn, "b", full(99))
    assert keys(conn) == set()


def test_interval_scales_the_expected_samples(conn):
    host_rows(conn, [95] * 11, start=600, step=60)  # 11 samples a minute apart
    assert keys(conn) == set()  # gap 60 > 2 x 15
    assert keys(conn, interval=60) == {"high_cpu:host"}


# disk


def test_disk_at_91_percent_fires(conn):
    disk_row(conn, T - 15, 91)
    conn.commit()
    (s,) = evaluate(conn, NOW)
    assert (s.key, s.rule, s.summary) == (
        "disk_filling:host",
        "disk_filling",
        "Storage at 91% used.",
    )


def test_disk_at_89_percent_does_not_fire(conn):
    disk_row(conn, T - 15, 89)
    conn.commit()
    assert keys(conn) == set()


def trend(conn, start_pct, per_hour, hours=6.0, noise=None):
    n = int(hours * 12)
    for i in range(n + 1):
        ts = T - int(hours * 3600) + i * 300
        pct = start_pct + per_hour * (ts - (T - hours * 3600)) / 3600
        if noise:
            pct += noise[i % len(noise)]
        disk_row(conn, ts, pct, res=300)
    conn.commit()


def clear(conn):
    conn.execute("DELETE FROM host_metrics")
    conn.commit()


def test_disk_trend_crossing_in_14_hours_fires(conn):
    trend(conn, 40.0, 3.0)  # 58% now, 42 points left at 3 a hour
    (s,) = evaluate(conn, NOW)
    assert s.key == "disk_filling:host"
    assert s.summary == "Storage at 58% used, rising 3.0 points an hour; full in about 14 h."


def test_disk_trend_horizon_both_sides(conn):
    trend(conn, 50.0, 2.0)  # 62% now, 19 h to go
    assert keys(conn) == {"disk_filling:host"}
    clear(conn)
    trend(conn, 30.0, 2.0)  # 42% now, 29 h to go
    assert keys(conn) == set()


def test_disk_trend_needs_two_hours_of_samples(conn):
    trend(conn, 60.0, 10.0, hours=1.5)  # steep, but only 1.5 h of history
    assert keys(conn) == set()
    conn.execute("DELETE FROM host_metrics")
    conn.commit()
    trend(conn, 60.0, 10.0, hours=2.0)
    assert keys(conn) == {"disk_filling:host"}


def test_disk_slope_threshold_both_sides(conn):
    trend(conn, 86.9, 0.49)  # 89.84% now, full in about 20.6 h, but under 0.5 an hour
    assert keys(conn) == set()
    clear(conn)
    trend(conn, 86.9, 0.5)  # 89.9% now, full in about 20 h
    assert keys(conn) == {"disk_filling:host"}


def test_disk_noisy_flat_line_does_not_fire(conn):
    trend(conn, 70.0, 0.0, noise=[0, 6, -6, 3, -3, 8, -8, 1])
    assert keys(conn) == set()


def test_disk_noisy_but_rising_line_has_low_r2(conn):
    # slope ~0.6/h but swamped by noise: R2 under 0.8
    trend(conn, 70.0, 0.6, noise=[0, 9, -9, 4, -4, 7, -7, 2, -2, 8, -8, 5])
    assert keys(conn) == set()


def test_disk_falling_line_does_not_fire(conn):
    trend(conn, 80.0, -2.0)
    assert keys(conn) == set()

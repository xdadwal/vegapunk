from datetime import timedelta

from tests.helpers import facts, host_sample, listing, now, obs, set_status


def test_host_requires_sign_in(client):
    assert client.get("/api/host").status_code == 401
    assert client.get("/api/host/metrics").status_code == 401


def test_host_before_any_capture_is_empty_not_an_error(signed_in):
    body = signed_in.get("/api/host").json()
    assert body["latest"] is None and body["hostname"] is None
    assert body["containers"] == {"total": 0, "running": 0, "needs_attention": 0}
    assert body["capture"]["interval_seconds"] == 15
    for source in ("host", "docker"):
        assert body["capture"]["sources"][source] == {
            "ok": False,
            "stale": False,
            "last_success_at": None,
            "error": None,
        }


def test_host_reports_latest_sample_facts_and_counts(signed_in, conn):
    t = now()
    facts(conn, t)
    host_sample(conn, t - timedelta(seconds=15), cpu=1.0)
    host_sample(conn, t, cpu=42.5, mem_used=600, mem_total=1_000)
    listing(
        conn,
        t,
        obs("a", "ok"),
        obs("b", "sick", health="unhealthy"),
        obs("c", "stopped", state="exited"),
        obs("d", "new", state="created"),
    )
    body = signed_in.get("/api/host").json()
    assert (body["hostname"], body["os"], body["docker_version"], body["cpu_count"]) == (
        "nas",
        "TrueNAS",
        "29.0",
        8,
    )
    assert body["boot_time"].endswith("Z")
    assert body["latest"] == {
        "observed_at": t.isoformat().replace("+00:00", "Z"),
        "cpu_pct": 42.5,
        "mem_used": 600,
        "mem_total": 1_000,
        "disk_used": 10,
        "disk_total": 100,
    }
    assert body["containers"] == {"total": 4, "running": 1, "needs_attention": 2}


def test_docker_error_is_reported_and_clears_on_recovery(signed_in, conn):
    t = now()
    set_status(conn, "docker", t, error="Can't reach Docker at unix:///x.")
    docker = signed_in.get("/api/host").json()["capture"]["sources"]["docker"]
    assert docker["ok"] is False and docker["error"] == "Can't reach Docker at unix:///x."
    set_status(conn, "docker", t + timedelta(seconds=15))
    docker = signed_in.get("/api/host").json()["capture"]["sources"]["docker"]
    assert docker["ok"] is True and docker["error"] is None and docker["last_success_at"]


def test_a_failing_source_keeps_its_last_success_time(signed_in, conn):
    t = now() - timedelta(seconds=5)
    set_status(conn, "docker", t)
    set_status(conn, "docker", t + timedelta(seconds=1), error="down")
    docker = signed_in.get("/api/host").json()["capture"]["sources"]["docker"]
    assert docker["ok"] is False and docker["error"] == "down"
    assert docker["last_success_at"] == t.isoformat().replace("+00:00", "Z")


def test_source_is_stale_after_three_intervals(signed_in, conn):
    set_status(conn, "host", now() - timedelta(seconds=30))
    set_status(conn, "docker", now() - timedelta(seconds=60))
    sources = signed_in.get("/api/host").json()["capture"]["sources"]
    assert sources["host"]["stale"] is False  # 30 s < 3 x 15 s
    assert sources["docker"]["stale"] is True


def test_source_that_never_ran_goes_stale_after_three_intervals_of_uptime(signed_in):
    assert signed_in.get("/api/host").json()["capture"]["sources"]["host"]["stale"] is False
    signed_in.app.state.started_at = now() - timedelta(seconds=100)
    assert signed_in.get("/api/host").json()["capture"]["sources"]["host"]["stale"] is True


def test_metrics_ranges_and_percentages(signed_in, conn):
    t = now()
    host_sample(conn, t - timedelta(minutes=30), cpu=10.04, mem_used=250, mem_total=1_000)
    host_sample(conn, t - timedelta(minutes=10), cpu=None, mem_used=500, mem_total=1_000)
    body = signed_in.get("/api/host/metrics?range=1h").json()
    assert (body["range"], body["resolution_seconds"]) == ("1h", 15)
    assert [(p["cpu_pct"], p["mem_pct"], p["disk_pct"]) for p in body["points"]] == [
        (10.0, 25.0, 10.0),
        (None, 50.0, 10.0),
    ]
    assert body["points"][0]["ts"] < body["points"][1]["ts"]
    assert body["points"][0]["ts"].endswith("Z")


def test_metrics_default_range_is_one_hour(signed_in):
    assert signed_in.get("/api/host/metrics").json()["range"] == "1h"


def test_metrics_1h_uses_raw_and_6h_uses_rollups(signed_in, conn):
    t = now()
    ts = int(t.timestamp()) - 600
    host_sample(conn, t - timedelta(minutes=10), cpu=11.0)  # raw
    conn.execute(
        "INSERT INTO host_metrics (res, ts, cpu_pct, mem_used, mem_total, disk_used, disk_total) "
        "VALUES (300, ?, 22.0, 1, 2, 1, 2)",
        (ts // 300 * 300,),
    )
    one_hour = signed_in.get("/api/host/metrics?range=1h").json()
    six_hours = signed_in.get("/api/host/metrics?range=6h").json()
    assert [p["cpu_pct"] for p in one_hour["points"]] == [11.0]
    assert [p["cpu_pct"] for p in six_hours["points"]] == [22.0]
    assert six_hours["resolution_seconds"] == 300


def test_long_ranges_are_hourly_and_capped(signed_in, conn):
    top = int(now().timestamp()) // 300 * 300
    conn.executemany(
        "INSERT INTO host_metrics (res, ts, cpu_pct, mem_used, mem_total, disk_used, disk_total) "
        "VALUES (300, ?, 50.0, 1, 2, 1, 2)",
        [(top - 300 * i,) for i in range(30 * 288)],
    )
    month = signed_in.get("/api/host/metrics?range=30d").json()
    week = signed_in.get("/api/host/metrics?range=7d").json()
    assert month["resolution_seconds"] == week["resolution_seconds"] == 3600
    assert len(month["points"]) <= 721 and len(week["points"]) <= 169
    assert all(p["cpu_pct"] == 50.0 for p in month["points"])


def test_metrics_with_no_samples_is_an_empty_list(signed_in):
    for r in ("1h", "6h", "24h", "7d", "30d"):
        assert signed_in.get(f"/api/host/metrics?range={r}").json()["points"] == []


def test_metrics_rejects_an_unknown_range(signed_in):
    assert signed_in.get("/api/host/metrics?range=1y").status_code == 422
    assert signed_in.get("/api/host/metrics?range=").status_code == 422

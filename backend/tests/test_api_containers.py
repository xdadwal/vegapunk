from datetime import timedelta

import pytest

from tests.helpers import listing, now, obs, sample


def test_containers_require_sign_in(client):
    assert client.get("/api/containers").status_code == 401
    assert client.get("/api/containers/web").status_code == 401


def test_nothing_stored_means_empty_list(signed_in):
    assert signed_in.get("/api/containers").json() == {"total": 0, "items": []}


def test_list_sorts_problems_first(signed_in, conn):
    listing(
        conn,
        now(),
        obs("1", "a-ok"),
        obs("2", "b-sick", health="unhealthy"),
        obs("3", "c-stopped", state="exited"),
        obs("4", "d-new", state="created"),
        obs("5", "0-ok"),
    )
    body = signed_in.get("/api/containers").json()
    assert body["total"] == 5
    assert [(i["name"], i["status"]) for i in body["items"]] == [
        ("c-stopped", "offline"),
        ("b-sick", "degraded"),
        ("d-new", "unknown"),
        ("0-ok", "online"),
        ("a-ok", "online"),
    ]


def test_item_shape_and_current_values(signed_in, conn):
    t = now()
    listing(conn, t, obs("abc", "web", health="healthy", restarts=2, started=t))
    sample(conn, t, "abc", cpu=12.5, mem=300, limit=1_000)
    (item,) = signed_in.get("/api/containers").json()["items"]
    assert item == {
        "id": "abc",
        "name": "web",
        "image": "img:1",
        "state": "running",
        "health": "healthy",
        "status": "online",
        "removed": False,
        "restart_count": 2,
        "started_at": t.isoformat().replace("+00:00", "Z"),
        "cpu_pct": 12.5,
        "mem_used": 300,
        "mem_limit": 1_000,
    }


def test_null_limit_and_cpu_are_passed_through(signed_in, conn):
    t = now()
    listing(conn, t, obs("a", "web"))
    sample(conn, t, "a", cpu=None, mem=5, limit=None)
    (item,) = signed_in.get("/api/containers").json()["items"]
    assert (item["cpu_pct"], item["mem_used"], item["mem_limit"]) == (None, 5, None)


def test_list_excludes_removed_containers(signed_in, conn):
    t = now()
    listing(conn, t, obs("a", "web"), obs("b", "gone"))
    listing(conn, t + timedelta(seconds=15), obs("a", "web"))
    names = [i["name"] for i in signed_in.get("/api/containers").json()["items"]]
    assert names == ["web"]


def test_same_name_appears_once_as_the_live_container(signed_in, conn):
    t = now()
    listing(conn, t - timedelta(hours=1), obs("old", "web", state="exited"))
    listing(conn, t, obs("old", "web", state="exited"), obs("new", "web"))
    body = signed_in.get("/api/containers").json()
    assert [(i["id"], i["state"]) for i in body["items"]] == [("new", "running")]
    assert body["total"] == 1


def test_old_samples_are_not_shown_as_current(signed_in, conn):
    t = now()
    listing(conn, t, obs("a", "stale"), obs("b", "fresh"))
    sample(conn, t - timedelta(seconds=120), "a", cpu=9.0)
    sample(conn, t - timedelta(seconds=10), "b", cpu=9.0)
    items = {i["name"]: i for i in signed_in.get("/api/containers").json()["items"]}
    assert items["stale"]["cpu_pct"] is None and items["stale"]["mem_used"] is None
    assert items["fresh"]["cpu_pct"] == 9.0


def test_only_the_newest_sample_counts(signed_in, conn):
    t = now()
    listing(conn, t, obs("a", "web"))
    sample(conn, t - timedelta(seconds=30), "a", cpu=1.0)
    sample(conn, t - timedelta(seconds=5), "a", cpu=2.0)
    assert signed_in.get("/api/containers").json()["items"][0]["cpu_pct"] == 2.0


def test_detail_by_name_with_metrics(signed_in, conn):
    t = now()
    listing(conn, t, obs("a", "web"), obs("b", "other"))
    sample(conn, t - timedelta(minutes=20), "a", cpu=1.0, mem=10)
    sample(conn, t - timedelta(seconds=5), "a", cpu=3.0, mem=30)
    sample(conn, t - timedelta(seconds=5), "b", cpu=99.0)
    body = signed_in.get("/api/containers/web").json()
    assert body["name"] == "web" and body["removed"] is False and body["cpu_pct"] == 3.0
    metrics = body["metrics"]
    assert (metrics["range"], metrics["resolution_seconds"]) == ("1h", 15)
    assert [(p["cpu_pct"], p["mem_used"]) for p in metrics["points"]] == [(1.0, 10), (3.0, 30)]


def test_detail_carries_the_servers_clock(signed_in, conn):
    listing(conn, now(), obs("a", "web"))
    stamp = signed_in.get("/api/containers/web").json()["now"]
    assert stamp.endswith("Z")


def test_detail_range_is_validated(signed_in, conn):
    listing(conn, now(), obs("a", "web"))
    assert signed_in.get("/api/containers/web?range=6h").json()["metrics"]["range"] == "6h"
    assert signed_in.get("/api/containers/web?range=nope").status_code == 422


def test_detail_of_a_removed_container_is_offline_and_marked_removed(signed_in, conn):
    t = now()
    listing(conn, t - timedelta(minutes=2), obs("a", "web"))
    sample(conn, t - timedelta(minutes=2), "a", cpu=4.0)
    listing(conn, t)  # nothing listed: it was removed
    body = signed_in.get("/api/containers/web").json()
    assert (body["removed"], body["status"], body["state"]) == (True, "offline", "running")
    assert (body["cpu_pct"], body["mem_used"], body["mem_limit"]) == (None, None, None)
    assert [p["cpu_pct"] for p in body["metrics"]["points"]] == [4.0]


def test_detail_prefers_the_live_container_over_an_old_one_with_the_same_name(signed_in, conn):
    t = now()
    listing(conn, t - timedelta(hours=1), obs("old", "web"))
    listing(conn, t, obs("new", "web"))
    assert signed_in.get("/api/containers/web").json()["id"] == "new"


def test_detail_accepts_dots_dashes_and_underscores(signed_in, conn):
    listing(conn, now(), obs("a", "my-app_1.web"))
    assert signed_in.get("/api/containers/my-app_1.web").json()["id"] == "a"


def test_detail_unknown_name_names_it_in_the_message(signed_in):
    r = signed_in.get("/api/containers/unknown")
    assert r.status_code == 404 and r.json() == {"detail": "No container named unknown"}


@pytest.mark.parametrize("name", ["-bad", "a%20b", ".x", "x" * 300])
def test_detail_invalid_or_unknown_name_is_404(signed_in, name):
    assert signed_in.get(f"/api/containers/{name}").status_code == 404


def test_names_are_matched_exactly_not_as_patterns(signed_in, conn):
    listing(conn, now(), obs("a", "web"))
    for probe in ("we%25", "w_b", "web%20", "WEB"):
        assert signed_in.get(f"/api/containers/{probe}").status_code == 404

from datetime import UTC, datetime
from pathlib import Path

import pytest

from vegapunk.capture.collectors import (
    SOURCES,
    DockerCollector,
    HostCollector,
    cpu_percent,
    memory_limit,
    memory_used,
)
from vegapunk.capture.records import ContainerList, ContainerSample, HostFacts, HostSample
from vegapunk.host.docker import DockerError

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_sources_are_named():
    assert SOURCES == ("host", "docker")


# ---- pure helpers
def test_cpu_percent_is_a_share_of_the_host():
    assert cpu_percent((100, 1_000), (150, 1_500)) == pytest.approx(10.0)


@pytest.mark.parametrize(
    "prev,cur",
    [
        (None, (150, 1_500)),
        ((100, 1_000), None),
        ((100, 1_000), (50, 1_500)),  # container counter went backwards (restart)
        ((100, 1_000), (150, 1_000)),  # no system time elapsed
        ((100, 1_000), (150, 900)),
    ],
)
def test_cpu_percent_is_none_when_it_cannot_be_computed(prev, cur):
    assert cpu_percent(prev, cur) is None


def test_cpu_percent_is_capped_at_100():
    assert cpu_percent((0, 0), (5_000, 1_000)) == 100.0


def test_memory_excludes_reclaimable_cache():
    assert memory_used({"usage": 1000, "stats": {"inactive_file": 400}}) == 600


def test_memory_prefers_total_inactive_file_like_docker():
    stats = {"usage": 1000, "stats": {"total_inactive_file": 300, "inactive_file": 100}}
    assert memory_used(stats) == 700


def test_memory_falls_back_to_cache_then_to_usage():
    assert memory_used({"usage": 1000, "stats": {"cache": 250}}) == 750
    assert memory_used({"usage": 1000, "stats": {}}) == 1000
    assert memory_used({"usage": 1000}) == 1000


def test_memory_never_goes_negative():
    assert memory_used({"usage": 100, "stats": {"inactive_file": 500}}) == 0


def test_memory_without_usage_is_none():
    assert memory_used({}) is None


def test_unlimited_memory_is_no_limit():
    assert memory_limit(8_000, host_mem_total=8_000) is None
    assert memory_limit(9_000, host_mem_total=8_000) is None
    assert memory_limit(None, host_mem_total=8_000) is None
    assert memory_limit(64, host_mem_total=8_000) == 64


# ---- Docker collector
def attrs(
    cid, name, state="running", health=None, restarts=0, started="2026-01-01T00:00:00.123456789Z"
):
    st = {"Status": state, "StartedAt": started}
    if health:
        st["Health"] = {"Status": health}
    return {
        "Id": cid,
        "Name": "/" + name,
        "Config": {"Image": "img:1"},
        "State": st,
        "RestartCount": restarts,
    }


def stats(cpu, system, usage=1_000, limit=64_000, inactive=0):
    return {
        "cpu_stats": {"cpu_usage": {"total_usage": cpu}, "system_cpu_usage": system},
        "memory_stats": {"usage": usage, "limit": limit, "stats": {"inactive_file": inactive}},
    }


class FakeDocker:
    def __init__(self, containers, stats_by_id=None):
        self.containers = containers
        self.stats_by_id = stats_by_id or {}
        self.stats_calls = []
        self.info_value = {
            "Name": "nas",
            "OperatingSystem": "TrueNAS",
            "KernelVersion": "6.1",
            "NCPU": 8,
            "MemTotal": 16_000_000,
        }

    def info(self):
        return self.info_value

    def version(self):
        return "29.0"

    def list_containers(self):
        if isinstance(self.containers, Exception):
            raise self.containers
        return self.containers

    def container_stats(self, cid):
        self.stats_calls.append(cid)
        value = self.stats_by_id.get(cid)
        if isinstance(value, Exception):
            raise value
        return value


def split(records):
    facts = [r for r in records if isinstance(r, HostFacts)]
    lists = [r for r in records if isinstance(r, ContainerList)]
    samples = {r.container_id: r for r in records if isinstance(r, ContainerSample)}
    return facts, lists, samples


def test_returns_facts_listing_and_one_sample_per_running_container():
    docker = FakeDocker(
        [attrs("a", "web"), attrs("b", "db", state="exited")],
        {"a": stats(100, 1_000, usage=2_000, inactive=500)},
    )
    facts, lists, samples = split(DockerCollector(docker).collect(NOW))
    assert facts == [HostFacts("nas", "TrueNAS", "6.1", "29.0", 8, 16_000_000)]
    assert [(c.id, c.name, c.state) for c in lists[0].items] == [
        ("a", "web", "running"),
        ("b", "db", "exited"),
    ]
    assert set(samples) == {"a"}
    assert (samples["a"].mem_used, samples["a"].mem_limit) == (1_500, 64_000)


def test_first_cycle_has_no_cpu_second_cycle_does():
    docker = FakeDocker([attrs("a", "web")], {"a": stats(100, 1_000)})
    collector = DockerCollector(docker)
    assert split(collector.collect(NOW))[2]["a"].cpu_pct is None
    docker.stats_by_id["a"] = stats(150, 1_500)
    assert split(collector.collect(NOW))[2]["a"].cpu_pct == pytest.approx(10.0)


def test_container_names_lose_their_leading_slash_and_start_time_is_parsed():
    docker = FakeDocker([attrs("a", "web"), attrs("b", "never", started="0001-01-01T00:00:00Z")])
    _, lists, _ = split(DockerCollector(docker).collect(NOW))
    web, never = lists[0].items
    assert web.name == "web" and web.started_at == datetime(2026, 1, 1, tzinfo=UTC).replace(
        microsecond=123456
    )
    assert never.started_at is None


def test_health_and_restart_count_are_carried():
    docker = FakeDocker([attrs("a", "web", health="unhealthy", restarts=3)], {"a": stats(1, 1)})
    item = split(DockerCollector(docker).collect(NOW))[1][0].items[0]
    assert (item.health, item.restart_count) == ("unhealthy", 3)


def test_containers_that_are_not_running_get_no_stats_call_and_no_sample():
    docker = FakeDocker(
        [
            attrs("a", "x", state="exited"),
            attrs("b", "y", state="restarting"),
            attrs("c", "z", state="created"),
        ]
    )
    _, lists, samples = split(DockerCollector(docker).collect(NOW))
    assert len(lists[0].items) == 3 and samples == {}
    assert docker.stats_calls == []


def test_container_that_vanishes_between_list_and_stats_is_skipped():
    docker = FakeDocker([attrs("a", "web"), attrs("b", "db")], {"a": None, "b": stats(1, 1)})
    _, lists, samples = split(DockerCollector(docker).collect(NOW))
    assert len(lists[0].items) == 2 and set(samples) == {"b"}


def test_stats_error_for_one_container_does_not_fail_the_cycle():
    docker = FakeDocker(
        [attrs("a", "web"), attrs("b", "db")], {"a": DockerError("boom"), "b": stats(1, 1)}
    )
    _, _, samples = split(DockerCollector(docker).collect(NOW))
    assert set(samples) == {"b"}


def test_running_container_with_missing_stats_fields_has_null_values():
    docker = FakeDocker([attrs("a", "web")], {"a": {}})
    sample = split(DockerCollector(docker).collect(NOW))[2]["a"]
    assert (sample.cpu_pct, sample.mem_used, sample.mem_limit) == (None, None, None)


def test_unlimited_memory_becomes_no_limit():
    docker = FakeDocker([attrs("a", "web")], {"a": stats(1, 1, limit=16_000_000)})
    assert split(DockerCollector(docker).collect(NOW))[2]["a"].mem_limit is None


def test_restarted_container_cpu_is_none_not_negative():
    docker = FakeDocker([attrs("a", "web")], {"a": stats(5_000, 1_000)})
    collector = DockerCollector(docker)
    collector.collect(NOW)
    docker.stats_by_id["a"] = stats(10, 2_000)  # the container restarted: its counter reset
    assert split(collector.collect(NOW))[2]["a"].cpu_pct is None


def test_listing_failure_raises_and_returns_nothing():
    docker = FakeDocker(DockerError("Can't reach Docker"))
    with pytest.raises(DockerError):
        DockerCollector(docker).collect(NOW)


def test_counters_of_vanished_containers_are_forgotten():
    docker = FakeDocker([attrs("a", "web")], {"a": stats(1, 1)})
    collector = DockerCollector(docker)
    collector.collect(NOW)
    docker.containers = []
    collector.collect(NOW)
    assert collector._cpu == {}


# ---- host collector
def test_host_collector_returns_one_sample():
    class FakeSystem:
        def cpu_percent(self):
            return 12.5

        def memory(self):
            return 4_000, 16_000

        def disk(self):
            return 500, 1_000

        def boot_time(self):
            return NOW

    assert HostCollector(FakeSystem()).collect(NOW) == [
        HostSample(12.5, 4_000, 16_000, 500, 1_000, NOW)
    ]


def test_host_collector_raises_when_the_disk_path_is_missing():
    from vegapunk.host.system import SystemHost

    with pytest.raises(FileNotFoundError):
        HostCollector(SystemHost(Path("/definitely/not/here"))).collect(NOW)


def test_oom_kill_and_exit_marker_are_carried():
    killed = attrs("a", "web", state="exited")
    killed["State"].update({"OOMKilled": True, "FinishedAt": "2026-01-02T03:04:05.5Z"})
    fine = attrs("b", "db")
    fine["State"].update({"OOMKilled": False, "FinishedAt": "0001-01-01T00:00:00Z"})
    plain = attrs("c", "x")  # neither key present
    docker = FakeDocker([killed, fine, plain], {"b": stats(1, 1), "c": stats(1, 1)})
    a, b, c = split(DockerCollector(docker).collect(NOW))[1][0].items
    assert (a.oom_killed, a.exit_marker) == (True, "2026-01-02T03:04:05.5Z")
    assert (b.oom_killed, b.exit_marker) == (False, None)
    assert (c.oom_killed, c.exit_marker) == (False, None)

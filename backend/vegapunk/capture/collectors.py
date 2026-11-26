"""Collectors turn what they read from the host into normalized records.

Everything downstream (ingest, storage, API, UI) sees only the records, so a new source
of data is a new collector and nothing else changes.
"""

from datetime import datetime
from typing import Protocol

from vegapunk.capture.records import (
    ContainerList,
    ContainerObservation,
    ContainerSample,
    HostFacts,
    HostSample,
    Record,
)
from vegapunk.config import Settings
from vegapunk.host.docker import DockerError, DockerHost
from vegapunk.host.system import SystemHost

SOURCES = ("host", "docker")


class Collector(Protocol):
    name: str

    def collect(self, now: datetime) -> list[Record]:
        """Read the source. Raises when it cannot be read."""
        ...


def cpu_percent(previous: tuple[int, int] | None, current: tuple[int, int] | None) -> float | None:
    """A container's share of the whole host's CPU, 0 to 100.

    Each tuple is (container CPU time, host CPU time) in nanoseconds. None when it cannot
    be known: no earlier reading, no time elapsed, or a counter that went backwards (the
    container restarted).
    """
    if previous is None or current is None:
        return None
    container = current[0] - previous[0]
    system = current[1] - previous[1]
    if system <= 0 or container < 0:
        return None
    return min(100.0, container / system * 100)


def memory_used(memory_stats: dict) -> int | None:
    """Memory in use, without reclaimable page cache: Docker's own convention."""
    usage = memory_stats.get("usage")
    if usage is None:
        return None
    detail = memory_stats.get("stats") or {}
    for key in ("total_inactive_file", "inactive_file", "cache"):
        if key in detail:
            return max(0, usage - detail[key])
    return usage


def memory_limit(limit: int | None, host_mem_total: int) -> int | None:
    """Docker reports the host's total as the limit when none is set."""
    if limit is None or limit >= host_mem_total:
        return None
    return limit


def _started_at(raw: str | None) -> datetime | None:
    if not raw or raw.startswith("0001-"):
        return None
    return datetime.fromisoformat(raw)


def _exit_marker(raw: str | None) -> str | None:
    if not raw or raw.startswith("0001-"):
        return None
    return raw


class HostCollector:
    name = "host"

    def __init__(self, system: SystemHost) -> None:
        self._system = system

    def collect(self, now: datetime) -> list[Record]:
        mem_used, mem_total = self._system.memory()
        disk_used, disk_total = self._system.disk()
        return [
            HostSample(
                cpu_pct=self._system.cpu_percent(),
                mem_used=mem_used,
                mem_total=mem_total,
                disk_used=disk_used,
                disk_total=disk_total,
                boot_time=self._system.boot_time(),
            )
        ]


class DockerCollector:
    name = "docker"

    def __init__(self, docker: DockerHost) -> None:
        self._docker = docker
        # Each running container's previous (container, host) CPU counters.
        self._cpu: dict[str, tuple[int, int]] = {}

    def collect(self, now: datetime) -> list[Record]:
        info = self._docker.info()
        facts = HostFacts(
            hostname=info.get("Name", ""),
            os=info.get("OperatingSystem", ""),
            kernel=info.get("KernelVersion", ""),
            docker_version=self._docker.version(),
            cpu_count=info.get("NCPU", 0),
            mem_total=info.get("MemTotal", 0),
        )
        listed = self._docker.list_containers()

        observations = []
        samples = []
        seen: set[str] = set()
        for a in listed:
            state = a["State"]
            observations.append(
                ContainerObservation(
                    id=a["Id"],
                    name=a["Name"].lstrip("/"),
                    image=a["Config"]["Image"],
                    state=state["Status"],
                    health=(state.get("Health") or {}).get("Status"),
                    restart_count=a.get("RestartCount", 0),
                    started_at=_started_at(state.get("StartedAt")),
                    oom_killed=bool(state.get("OOMKilled", False)),
                    exit_marker=_exit_marker(state.get("FinishedAt")),
                )
            )
            if state["Status"] == "running":
                sample = self._sample(a["Id"], facts.mem_total, seen)
                if sample is not None:
                    samples.append(sample)

        # Forget the counters of containers that are gone or no longer running.
        self._cpu = {cid: v for cid, v in self._cpu.items() if cid in seen}
        return [facts, ContainerList(tuple(observations)), *samples]

    def _sample(self, cid: str, host_mem_total: int, seen: set[str]) -> ContainerSample | None:
        try:
            stats = self._docker.container_stats(cid)
        except DockerError:
            return None  # one container failing must not fail the cycle
        if stats is None:
            return None  # deleted between the listing and now
        cpu = stats.get("cpu_stats") or {}
        total = (cpu.get("cpu_usage") or {}).get("total_usage")
        system = cpu.get("system_cpu_usage")
        counters = (total, system) if total is not None and system is not None else None
        pct = cpu_percent(self._cpu.get(cid), counters)
        if counters is not None:
            self._cpu[cid] = counters
            seen.add(cid)
        memory = stats.get("memory_stats") or {}
        return ContainerSample(
            container_id=cid,
            cpu_pct=pct,
            mem_used=memory_used(memory),
            mem_limit=memory_limit(memory.get("limit"), host_mem_total),
        )


def build_collectors(settings: Settings) -> list[Collector]:
    return [
        HostCollector(SystemHost(settings.disk_path)),
        DockerCollector(DockerHost(settings.docker_url)),
    ]

"""Normalized records. Collectors produce them; ingest stores them; nothing downstream
needs to know which collector (or, later, which kind of source) they came from."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class HostFacts:
    hostname: str
    os: str
    kernel: str
    docker_version: str
    cpu_count: int
    mem_total: int


@dataclass(frozen=True)
class HostSample:
    cpu_pct: float | None
    mem_used: int
    mem_total: int
    disk_used: int
    disk_total: int
    boot_time: datetime


@dataclass(frozen=True)
class ContainerObservation:
    id: str
    name: str  # Docker's leading "/" removed
    image: str
    state: str
    health: str | None
    restart_count: int
    started_at: datetime | None


@dataclass(frozen=True)
class ContainerList:
    """A complete listing: a stored container missing from it is marked removed."""

    items: tuple[ContainerObservation, ...]


@dataclass(frozen=True)
class ContainerSample:
    container_id: str
    cpu_pct: float | None
    mem_used: int | None
    mem_limit: int | None


Record = HostFacts | HostSample | ContainerList | ContainerSample

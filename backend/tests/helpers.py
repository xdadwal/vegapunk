"""Seeding helpers for API tests: put data in through the real ingest path."""

from datetime import UTC, datetime, timedelta

from vegapunk.capture.ingest import ingest
from vegapunk.capture.records import (
    ContainerList,
    ContainerObservation,
    ContainerSample,
    HostFacts,
    HostSample,
)
from vegapunk.db import transaction


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def obs(cid, name, state="running", health=None, restarts=0, started=None):
    return ContainerObservation(cid, name, "img:1", state, health, restarts, started)


def listing(conn, at, *items):
    ingest(conn, at, [ContainerList(tuple(items))])


def sample(conn, at, cid, cpu=5.0, mem=100, limit=1_000):
    ingest(conn, at, [ContainerSample(cid, cpu, mem, limit)])


def host_sample(conn, at, cpu=10.0, mem_used=250, mem_total=1_000, disk_used=10, disk_total=100):
    ingest(
        conn,
        at,
        [HostSample(cpu, mem_used, mem_total, disk_used, disk_total, at - timedelta(days=2))],
    )


def facts(conn, at):
    ingest(conn, at, [HostFacts("nas", "TrueNAS", "6.1", "29.0", 8, 16_000)])


def set_status(conn, source, at, error=None):
    with transaction(conn):
        if error is None:
            conn.execute(
                "INSERT INTO capture_status VALUES (?, ?, ?, NULL) ON CONFLICT (source) DO UPDATE "
                "SET last_attempt_at = excluded.last_attempt_at, "
                "last_success_at = excluded.last_success_at, last_error = NULL",
                (source, at.isoformat(), at.isoformat()),
            )
        else:
            conn.execute(
                "INSERT INTO capture_status VALUES (?, ?, NULL, ?) ON CONFLICT (source) DO UPDATE "
                "SET last_attempt_at = excluded.last_attempt_at, last_error = excluded.last_error",
                (source, at.isoformat(), error),
            )

"""The one place captured data enters the database.

It depends only on the normalized records, never on how they were captured, and it is
safe to repeat: the same records at the same time leave the same rows.
"""

import sqlite3
from collections.abc import Iterable
from datetime import datetime

from vegapunk.capture.records import (
    ContainerList,
    ContainerSample,
    HostFacts,
    HostSample,
    Record,
)
from vegapunk.db import transaction


def _store_facts(conn: sqlite3.Connection, r: HostFacts) -> None:
    conn.execute(
        "INSERT INTO host (id, hostname, os, kernel, docker_version, cpu_count, mem_total) "
        "VALUES (1, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET "
        "hostname = excluded.hostname, os = excluded.os, kernel = excluded.kernel, "
        "docker_version = excluded.docker_version, cpu_count = excluded.cpu_count, "
        "mem_total = excluded.mem_total",
        (r.hostname, r.os, r.kernel, r.docker_version, r.cpu_count, r.mem_total),
    )


def _store_host_sample(conn: sqlite3.Connection, ts: int, r: HostSample) -> None:
    conn.execute(
        "INSERT INTO host (id, boot_time) VALUES (1, ?) "
        "ON CONFLICT (id) DO UPDATE SET boot_time = excluded.boot_time",
        (r.boot_time.isoformat(),),
    )
    conn.execute(
        "INSERT OR REPLACE INTO host_metrics "
        "(res, ts, cpu_pct, mem_used, mem_total, disk_used, disk_total) "
        "VALUES (0, ?, ?, ?, ?, ?, ?)",
        (ts, r.cpu_pct, r.mem_used, r.mem_total, r.disk_used, r.disk_total),
    )


def _store_container_list(conn: sqlite3.Connection, ts: int, at: str, r: ContainerList) -> None:
    for c in r.items:
        stored = conn.execute(
            "SELECT restart_count FROM containers WHERE id = ?", (c.id,)
        ).fetchone()
        if stored is not None and stored["restart_count"] != c.restart_count:
            conn.execute(
                "INSERT OR REPLACE INTO container_restarts (container_id, ts, restart_count) "
                "VALUES (?, ?, ?)",
                (c.id, ts, c.restart_count),
            )
        conn.execute(
            "INSERT INTO containers (id, name, image, state, health, restart_count, started_at, "
            "oom_killed, exit_marker, first_seen_at, last_seen_at, removed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL) "
            "ON CONFLICT (id) DO UPDATE SET name = excluded.name, image = excluded.image, "
            "state = excluded.state, health = excluded.health, "
            "restart_count = excluded.restart_count, started_at = excluded.started_at, "
            "oom_killed = excluded.oom_killed, exit_marker = excluded.exit_marker, "
            "last_seen_at = excluded.last_seen_at, removed_at = NULL",
            (
                c.id,
                c.name,
                c.image,
                c.state,
                c.health,
                c.restart_count,
                c.started_at.isoformat() if c.started_at else None,
                int(c.oom_killed),
                c.exit_marker,
                at,
                at,
            ),
        )
    ids = [c.id for c in r.items]
    marks = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE containers SET removed_at = ? WHERE removed_at IS NULL AND id NOT IN ({marks})",
        (at, *ids),
    )


def _store_container_sample(conn: sqlite3.Connection, ts: int, r: ContainerSample) -> None:
    # A sample for a container we have never listed is dropped rather than orphaned.
    conn.execute(
        "INSERT OR REPLACE INTO container_metrics "
        "(container_id, res, ts, cpu_pct, mem_used, mem_limit) "
        "SELECT ?, 0, ?, ?, ?, ? WHERE EXISTS (SELECT 1 FROM containers WHERE id = ?)",
        (r.container_id, ts, r.cpu_pct, r.mem_used, r.mem_limit, r.container_id),
    )


def ingest(conn: sqlite3.Connection, observed_at: datetime, records: Iterable[Record]) -> None:
    ts = int(observed_at.timestamp())
    at = observed_at.isoformat()
    # Samples go last so they can refer to containers listed in the same call.
    ordered = sorted(records, key=lambda r: isinstance(r, ContainerSample))
    with transaction(conn):
        for record in ordered:
            match record:
                case HostFacts():
                    _store_facts(conn, record)
                case HostSample():
                    _store_host_sample(conn, ts, record)
                case ContainerList():
                    _store_container_list(conn, ts, at, record)
                case ContainerSample():
                    _store_container_sample(conn, ts, record)
                case _:
                    raise TypeError(f"Unknown record type: {type(record).__name__}")

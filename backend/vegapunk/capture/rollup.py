"""Short-term detail, long-term history.

Raw samples (res 0) are kept for a day. Five-minute averages (res 300) are kept for thirty
days. Roll-ups are rewritten each cycle for the last few buckets, so repeating them is
harmless and the current, partial bucket stays up to date.
"""

import sqlite3
from datetime import datetime, timedelta

from vegapunk.db import transaction
from vegapunk.timefmt import z

BUCKET = 300
RAW_KEEP = 24 * 3600
ROLLUP_KEEP = 30 * 86_400
RESTART_KEEP = 30 * 86_400
INVESTIGATION_KEEP = 7 * 86_400
FINDING_KEEP = 90 * 86_400
RECENT = 600  # roll up the buckets touching the last ten minutes


def rollup(conn: sqlite3.Connection, now: datetime) -> None:
    end = int(now.timestamp())
    start = (end - RECENT) // BUCKET * BUCKET
    with transaction(conn):
        conn.execute(
            "INSERT OR REPLACE INTO host_metrics "
            "(res, ts, cpu_pct, mem_used, mem_total, disk_used, disk_total) "
            "SELECT ?, ts / ? * ?, AVG(cpu_pct), CAST(ROUND(AVG(mem_used)) AS INTEGER), "
            "CAST(ROUND(AVG(mem_total)) AS INTEGER), CAST(ROUND(AVG(disk_used)) AS INTEGER), "
            "CAST(ROUND(AVG(disk_total)) AS INTEGER) "
            "FROM host_metrics WHERE res = 0 AND ts >= ? AND ts <= ? GROUP BY ts / ?",
            (BUCKET, BUCKET, BUCKET, start, end, BUCKET),
        )
        conn.execute(
            "INSERT OR REPLACE INTO container_metrics "
            "(container_id, res, ts, cpu_pct, mem_used, mem_limit) "
            "SELECT container_id, ?, ts / ? * ?, AVG(cpu_pct), "
            "CAST(ROUND(AVG(mem_used)) AS INTEGER), CAST(ROUND(AVG(mem_limit)) AS INTEGER) "
            "FROM container_metrics WHERE res = 0 AND ts >= ? AND ts <= ? "
            "GROUP BY container_id, ts / ?",
            (BUCKET, BUCKET, BUCKET, start, end, BUCKET),
        )


def prune(conn: sqlite3.Connection, now: datetime) -> None:
    """Delete what is past its retention, measured from now. Newer rows are never touched,
    however long the gap since the previous sample."""
    raw_cutoff = int((now - timedelta(seconds=RAW_KEEP)).timestamp())
    rollup_cutoff = int((now - timedelta(seconds=ROLLUP_KEEP)).timestamp())
    removed_cutoff = (now - timedelta(seconds=ROLLUP_KEEP)).isoformat()
    with transaction(conn):
        for table in ("host_metrics", "container_metrics"):
            conn.execute(f"DELETE FROM {table} WHERE res = 0 AND ts < ?", (raw_cutoff,))
            conn.execute(f"DELETE FROM {table} WHERE res = ? AND ts < ?", (BUCKET, rollup_cutoff))
        conn.execute(
            "DELETE FROM container_restarts WHERE ts < ?",
            (int((now - timedelta(seconds=RESTART_KEEP)).timestamp()),),
        )
        for table in ("container_metrics", "container_restarts"):
            conn.execute(
                f"DELETE FROM {table} WHERE container_id IN "
                "(SELECT id FROM containers WHERE removed_at IS NOT NULL AND removed_at < ?)",
                (removed_cutoff,),
            )
        conn.execute(
            "DELETE FROM investigations WHERE started_at < ?",
            (z(now - timedelta(seconds=INVESTIGATION_KEEP)),),
        )
        # Open findings are kept at any age; evidence goes by ON DELETE CASCADE.
        conn.execute(
            "DELETE FROM findings WHERE (status = 'resolved' AND resolved_at < ?) "
            "OR (status = 'reviewed' AND reviewed_at < ?)",
            (z(now - timedelta(seconds=FINDING_KEEP)),) * 2,
        )
        conn.execute(
            "DELETE FROM containers WHERE removed_at IS NOT NULL AND removed_at < ?",
            (removed_cutoff,),
        )

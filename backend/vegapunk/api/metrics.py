"""Time series for the charts, from raw samples or roll-ups."""

import sqlite3
from datetime import UTC, datetime
from typing import Literal

from vegapunk.api.queries import from_ts
from vegapunk.capture.rollup import BUCKET

Range = Literal["1h", "6h", "24h", "7d", "30d"]

HOUR = 3600
# range -> (seconds back, source resolution, output bucket in seconds or None for as-stored)
_RANGES: dict[str, tuple[int, int, int | None]] = {
    "1h": (HOUR, 0, None),
    "6h": (6 * HOUR, BUCKET, None),
    "24h": (24 * HOUR, BUCKET, None),
    "7d": (7 * 24 * HOUR, BUCKET, HOUR),  # hourly, so no range returns more than ~720 points
    "30d": (30 * 24 * HOUR, BUCKET, HOUR),
}


def series(
    conn: sqlite3.Connection,
    *,
    table: str,
    key_sql: str,
    key_params: tuple,
    columns: tuple[str, ...],
    range_: str,
    raw_interval: int,
) -> tuple[int, list[dict]]:
    """Returns (resolution_seconds, rows). Each row has ts (ISO) and the requested columns."""
    seconds, source_res, bucket = _RANGES[range_]
    since = int(datetime.now(UTC).timestamp()) - seconds
    resolution = raw_interval if source_res == 0 else (bucket or source_res)
    if bucket is None:
        select = ", ".join(columns)
        sql = (
            f"SELECT ts, {select} FROM {table} WHERE {key_sql} AND res = ? AND ts >= ? ORDER BY ts"
        )
    else:
        select = ", ".join(f"AVG({c}) AS {c}" for c in columns)
        sql = (
            f"SELECT ts / {bucket} * {bucket} AS ts, {select} FROM {table} "
            f"WHERE {key_sql} AND res = ? AND ts >= ? GROUP BY ts / {bucket} ORDER BY 1"
        )
    rows = conn.execute(sql, (*key_params, source_res, since)).fetchall()
    return resolution, [{"ts": from_ts(r["ts"]), **{c: r[c] for c in columns}} for r in rows]

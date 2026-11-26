"""The watch rules: plain reads of stored data that say when something needs a look.

Rules never call the model and never read the clock; `now` is passed in. They return signals,
and the runner decides whether to investigate.
"""

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from vegapunk.watch.findings import used_markers

RESTARTS = 3
RESTART_WINDOW = timedelta(minutes=15)
HIGH = 90  # percent, for CPU and memory
SUSTAIN = timedelta(minutes=10)
COVERAGE = 0.8  # share of the expected samples that must be present
MAX_GAP_INTERVALS = 2  # largest allowed gap between samples, in capture intervals
DISK_HIGH = 90  # percent used
DISK_HORIZON = timedelta(hours=24)
DISK_TREND_WINDOW = timedelta(hours=6)
DISK_MIN_HISTORY = timedelta(hours=2)
DISK_MIN_SLOPE = 0.5  # percentage points an hour
DISK_MIN_R2 = 0.8
ROLLUP_RES = 300

Rule = Literal["restart_loop", "oom_kill", "high_cpu", "high_memory", "disk_filling"]


@dataclass(frozen=True)
class Signal:
    key: str
    rule: Rule
    subject: str
    summary: str
    marker: str | None = None  # set by oom_kill, so the runner can record it


def _signal(rule: Rule, subject: str, summary: str, marker: str | None = None) -> Signal:
    return Signal(f"{rule}:{subject}", rule, subject, summary, marker)


def evaluate(conn: sqlite3.Connection, now: datetime, interval: int = 15) -> list[Signal]:
    ts = int(now.timestamp())
    signals: list[Signal] = []
    signals += _restart_loops(conn, ts)
    signals += _oom_kills(conn)
    signals += _host_sustained(conn, ts, interval)
    signals += _container_memory(conn, ts, interval)
    signals += _disk(conn, ts)
    return signals


def _current_containers(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """One row per name: the most recently seen row that is not removed (an old container and
    its replacement can share a name)."""
    rows = conn.execute(
        "SELECT * FROM containers WHERE removed_at IS NULL ORDER BY name, last_seen_at, id"
    ).fetchall()
    latest = {r["name"]: r for r in rows}  # later rows overwrite earlier ones
    return list(latest.values())


def _restart_loops(conn: sqlite3.Connection, now: int) -> list[Signal]:
    since = now - int(RESTART_WINDOW.total_seconds())
    out = []
    for c in _current_containers(conn):
        rows = conn.execute(
            "SELECT ts, restart_count, previous_count FROM container_restarts "
            "WHERE container_id = ? AND ts >= ? AND ts <= ? ORDER BY ts",
            (c["id"], since, now),
        ).fetchall()
        n = sum(max(0, r["restart_count"] - r["previous_count"]) for r in rows)
        if n >= RESTARTS:
            minutes = max(1, round((now - rows[0]["ts"]) / 60))
            out.append(_signal("restart_loop", c["name"], f"Restarted {n} times in {minutes} min."))
    return out


def _oom_kills(conn: sqlite3.Connection) -> list[Signal]:
    out = []
    for c in _current_containers(conn):
        if not c["oom_killed"] or c["exit_marker"] is None:
            continue
        key = f"oom_kill:{c['name']}"
        if c["exit_marker"] not in used_markers(conn, key):
            out.append(
                _signal(
                    "oom_kill", c["name"], "Killed for running out of memory.", c["exit_marker"]
                )
            )
    return out


def _sustained(samples: list[tuple[int, float | None]], now: int, interval: int) -> float | None:
    """The latest value if every sample over the last SUSTAIN is at least HIGH, with enough
    coverage and no large gap; otherwise None. `samples` are (ts, percent), oldest first."""
    start = now - int(SUSTAIN.total_seconds())
    window = [(t, v) for t, v in samples if start <= t <= now]
    if len(window) < COVERAGE * (SUSTAIN.total_seconds() / interval):
        return None
    if any(v is None or v < HIGH for _, v in window):
        return None
    max_gap = MAX_GAP_INTERVALS * interval
    edges = [start] + [t for t, _ in window] + [now]
    if any(b - a > max_gap for a, b in zip(edges, edges[1:], strict=False)):
        return None
    return window[-1][1]


def _pct(used: int | None, total: int | None) -> float | None:
    if used is None or not total:
        return None
    return used * 100 / total


def _host_sustained(conn: sqlite3.Connection, now: int, interval: int) -> list[Signal]:
    since = now - int(SUSTAIN.total_seconds())
    rows = conn.execute(
        "SELECT ts, cpu_pct, mem_used, mem_total FROM host_metrics "
        "WHERE res = 0 AND ts >= ? AND ts <= ? ORDER BY ts",
        (since, now),
    ).fetchall()
    out = []
    cpu = _sustained([(r["ts"], r["cpu_pct"]) for r in rows], now, interval)
    if cpu is not None:
        out.append(_signal("high_cpu", "host", f"CPU at {cpu:.0f}% for 10 min."))
    mem = _sustained([(r["ts"], _pct(r["mem_used"], r["mem_total"])) for r in rows], now, interval)
    if mem is not None:
        out.append(_signal("high_memory", "host", f"Memory at {mem:.0f}% for 10 min."))
    return out


def _container_memory(conn: sqlite3.Connection, now: int, interval: int) -> list[Signal]:
    since = now - int(SUSTAIN.total_seconds())
    out = []
    for c in _current_containers(conn):
        if c["state"] != "running":
            continue
        rows = conn.execute(
            "SELECT ts, mem_used, mem_limit FROM container_metrics "
            "WHERE container_id = ? AND res = 0 AND ts >= ? AND ts <= ? ORDER BY ts",
            (c["id"], since, now),
        ).fetchall()
        if not rows or not rows[-1]["mem_limit"]:
            continue
        mem = _sustained(
            [(r["ts"], _pct(r["mem_used"], r["mem_limit"])) for r in rows], now, interval
        )
        if mem is not None:
            limit = rows[-1]["mem_limit"] // (1024 * 1024)
            out.append(
                _signal(
                    "high_memory",
                    c["name"],
                    f"Memory at {mem:.0f}% of its {limit} MiB limit for 10 min.",
                )
            )
    return out


def _disk(conn: sqlite3.Connection, now: int) -> list[Signal]:
    latest = conn.execute(
        "SELECT disk_used, disk_total FROM host_metrics WHERE res = 0 AND ts <= ? "
        "AND disk_used IS NOT NULL AND disk_total > 0 ORDER BY ts DESC LIMIT 1",
        (now,),
    ).fetchone()
    if latest:
        pct = latest["disk_used"] * 100 / latest["disk_total"]
        if pct >= DISK_HIGH:
            return [_signal("disk_filling", "host", f"Storage at {pct:.0f}% used.")]
    rows = conn.execute(
        "SELECT ts, disk_used, disk_total FROM host_metrics WHERE res = ? AND ts >= ? AND ts <= ? "
        "AND disk_used IS NOT NULL AND disk_total > 0 ORDER BY ts",
        (ROLLUP_RES, now - int(DISK_TREND_WINDOW.total_seconds()), now),
    ).fetchall()
    if len(rows) < 2 or rows[-1]["ts"] - rows[0]["ts"] < DISK_MIN_HISTORY.total_seconds():
        return []
    xs = [(r["ts"] - now) / 3600 for r in rows]  # hours relative to now
    ys = [r["disk_used"] * 100 / r["disk_total"] for r in rows]
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True))
    if sxx == 0 or syy == 0:
        return []
    slope = sxy / sxx
    if slope < DISK_MIN_SLOPE - 1e-9 or sxy * sxy / (sxx * syy) < DISK_MIN_R2:
        return []
    current = my - slope * mx  # the fitted line at now (x = 0)
    hours = (100 - current) / slope
    if hours > DISK_HORIZON.total_seconds() / 3600:
        return []
    return [
        _signal(
            "disk_filling",
            "host",
            f"Storage at {current:.0f}% used, rising {slope:.1f} points an hour; "
            f"full in about {max(hours, 0):.0f} h.",
        )
    ]

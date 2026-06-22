"""A tool that reports the machine's live resource usage (CPU, RAM, temperatures).

Readings come from psutil (already a project dependency, shared with the battery
tool). Like the other tools it stays factual — just the raw numbers the brain can
reason over.
"""

from __future__ import annotations

import json

import psutil

from .registry import tool


def _read_temperatures() -> dict[str, float]:
    """Best-effort component temperatures in Celsius, keyed by sensor label.

    Returns an empty dict when the platform can't expose sensors: psutil only
    implements ``sensors_temperatures`` on Linux/FreeBSD, so on macOS/Windows the
    attribute is absent (or the call raises) — we report nothing rather than guess.
    """
    read = getattr(psutil, "sensors_temperatures", None)
    if read is None:
        return {}
    try:
        readings = read()
    except (NotImplementedError, OSError, AttributeError):
        return {}
    temps: dict[str, float] = {}
    for group, entries in readings.items():
        for entry in entries:
            if entry.current is not None:
                temps[entry.label or group] = round(entry.current, 1)
    return temps


@tool
def get_system_stats() -> str:
    """Report current system resource usage as JSON: CPU %, RAM, and temperatures.

    Call this when the user asks how the machine or system is doing, or when a task
    needs live performance metrics (CPU load, free memory) to decide what to do.
    Temperatures are included when the OS exposes sensors (e.g. Linux); on platforms
    without sensor support that field is an empty object.
    """
    mem = psutil.virtual_memory()
    stats = {
        "cpu_usage_percent": psutil.cpu_percent(interval=0.1),
        "ram_usage_percent": mem.percent,
        "ram_total_gb": round(mem.total / (1024**3), 2),
        "ram_used_gb": round(mem.used / (1024**3), 2),
        "temperatures_celsius": _read_temperatures(),
    }
    return json.dumps(stats)

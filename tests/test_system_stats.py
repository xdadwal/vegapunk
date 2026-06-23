"""Tests for the system_stats tool — deterministic via mocked psutil.

Live psutil readings (CPU/RAM/temperatures) are non-deterministic and
platform-specific, so we patch them to fixed values and pin the tool's contract:
valid JSON containing every metric its docstring (the model-facing description)
promises, plus the graceful empty-temperatures fallback on platforms without
sensor support (e.g. macOS).
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from vegapunk.tools.system_stats import get_system_stats

_BASE = "vegapunk.tools.system_stats.psutil"


def _patch_cpu_ram(monkeypatch):
    monkeypatch.setattr(f"{_BASE}.cpu_percent", lambda interval=0.1: 12.5)
    monkeypatch.setattr(
        f"{_BASE}.virtual_memory",
        lambda: SimpleNamespace(percent=40.0, total=16 * 1024**3, used=6 * 1024**3),
    )


def test_returns_cpu_ram_and_parsed_temperatures(monkeypatch):
    _patch_cpu_ram(monkeypatch)
    # raising=False: the attribute is absent on macOS, so we add it for the test.
    monkeypatch.setattr(
        f"{_BASE}.sensors_temperatures",
        lambda: {"coretemp": [SimpleNamespace(label="Core 0", current=45.5)]},
        raising=False,
    )

    data = json.loads(get_system_stats())  # must be valid JSON

    assert data["cpu_usage_percent"] == 12.5
    assert data["ram_usage_percent"] == 40.0
    assert data["ram_total_gb"] == 16.0
    assert data["ram_used_gb"] == 6.0
    assert data["temperatures_celsius"] == {"Core 0": 45.5}


def test_temperatures_empty_when_platform_has_no_sensors(monkeypatch):
    _patch_cpu_ram(monkeypatch)
    # Simulate macOS/Windows where psutil exposes no sensor API.
    monkeypatch.delattr(f"{_BASE}.sensors_temperatures", raising=False)

    data = json.loads(get_system_stats())

    assert data["temperatures_celsius"] == {}
    assert data["cpu_usage_percent"] == 12.5  # the rest still reported

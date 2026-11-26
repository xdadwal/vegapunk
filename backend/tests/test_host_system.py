from collections import namedtuple
from datetime import UTC, datetime
from pathlib import Path

import psutil

from vegapunk.host.system import SystemHost


def test_first_cpu_reading_is_none_then_a_number(monkeypatch):
    readings = iter([0.0, 12.5])
    monkeypatch.setattr(psutil, "cpu_percent", lambda interval=None: next(readings))
    host = SystemHost(Path("/"))
    assert host.cpu_percent() is None
    assert host.cpu_percent() == 12.5


def test_memory_used_is_total_minus_available(monkeypatch):
    vm = namedtuple("vm", "total available")(total=1000, available=250)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: vm)
    assert SystemHost(Path("/")).memory() == (750, 1000)


def test_disk_reads_the_configured_path(monkeypatch):
    seen = []
    usage = namedtuple("usage", "total used free")

    def fake(path):
        seen.append(path)
        return usage(total=100, used=40, free=60)

    monkeypatch.setattr("vegapunk.host.system.shutil.disk_usage", fake)
    assert SystemHost(Path("/host/data")).disk() == (40, 100)
    assert seen == [Path("/host/data")]


def test_boot_time_is_aware_utc(monkeypatch):
    monkeypatch.setattr(psutil, "boot_time", lambda: 1_800_000_000.0)
    boot = SystemHost(Path("/")).boot_time()
    assert boot == datetime.fromtimestamp(1_800_000_000, UTC)
    assert boot.tzinfo is not None


def test_construction_does_not_touch_proc(monkeypatch):
    def boom(*args, **kwargs):
        raise FileNotFoundError("/proc/stat")

    for name in ("cpu_percent", "virtual_memory", "boot_time"):
        monkeypatch.setattr(psutil, name, boom)
    SystemHost(Path("/"))

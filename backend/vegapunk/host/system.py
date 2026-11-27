"""Host CPU, memory, storage and boot time.

A container's own /proc already reports host-wide CPU and memory, so nothing from the
host needs mounting for these.
"""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import psutil


class SystemHost:
    def __init__(self, disk_path: Path) -> None:
        self._disk_path = disk_path
        self._primed = False

    def cpu_percent(self) -> float | None:
        """Host-wide CPU use, 0 to 100, since the previous call. None on the first call."""
        value = psutil.cpu_percent(interval=None)
        if not self._primed:
            self._primed = True
            return None
        return value

    def memory(self) -> tuple[int, int]:
        vm = psutil.virtual_memory()
        return vm.total - vm.available, vm.total

    def disk(self) -> tuple[int, int]:
        usage = shutil.disk_usage(self._disk_path)
        return usage.used, usage.total

    def boot_time(self) -> datetime:
        return datetime.fromtimestamp(psutil.boot_time(), UTC)

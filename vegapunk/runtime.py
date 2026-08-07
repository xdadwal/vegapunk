"""Shared Logpose execution policy and content-free runtime logs.

This module deliberately has no renderer dependency. Logpose calls runtime
observers from its own worker loop, while Vegapunk's prompt owns the terminal;
the only sink here is a rotating JSON Lines file for later inspection.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

from logpose import RetryPolicy, RuntimeEvent, RuntimeObserver

from .config import Config, config

RuntimeRole = Literal["interactive", "scheduler"]

_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUP_COUNT = 3


def runtime_log_path(cfg: Config, role: RuntimeRole) -> Path:
    """Return the process-specific JSONL file beside the configured database."""
    name = {
        "interactive": "vegapunk-runtime.jsonl",
        "scheduler": "scheduler-runtime.jsonl",
    }[role]
    return cfg.db_file.parent / name


class JsonlRuntimeObserver:
    """Append Logpose's metadata-only lifecycle events to a rotating JSONL file."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._handler = RotatingFileHandler(
            path,
            maxBytes=_LOG_MAX_BYTES,
            backupCount=_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        self._handler.setFormatter(logging.Formatter("%(message)s"))

    def __call__(self, event: RuntimeEvent) -> None:
        # RuntimeEvent intentionally has no prompt, response, tool argument,
        # tool-result, exception-message, or credential field. Serialising its
        # complete dataclass keeps the schema version intact without inventing a
        # second telemetry contract in Vegapunk.
        record = logging.LogRecord(
            "vegapunk.runtime",
            logging.INFO,
            __file__,
            0,
            json.dumps(asdict(event), separators=(",", ":"), sort_keys=True),
            (),
            None,
        )
        self._handler.handle(record)


@lru_cache(maxsize=None)
def _observer_for(path: str) -> JsonlRuntimeObserver:
    """Reuse one locked logging handler for every agent in this process."""
    return JsonlRuntimeObserver(Path(path))


def runtime_observer(cfg: Config = config, role: RuntimeRole = "interactive") -> RuntimeObserver:
    """Return the file-only observer for one Vegapunk process role."""
    return _observer_for(str(runtime_log_path(cfg, role)))


def agent_runtime_options(
    cfg: Config = config, role: RuntimeRole = "interactive"
) -> dict[str, object]:
    """Return the Logpose policy shared by interactive and scheduled agents."""
    return {
        "retry_policy": RetryPolicy(max_attempts=cfg.provider_max_attempts),
        "provider_turn_timeout": cfg.provider_turn_timeout,
        "max_concurrent_tools": cfg.max_concurrent_tools,
        "tool_timeout": cfg.tool_timeout,
        "tool_error_mode": "safe",
        "observers": (runtime_observer(cfg, role),),
    }

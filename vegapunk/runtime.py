"""Shared Logpose execution policy and content-free runtime logs.

This module deliberately has no renderer dependency. It configures the
``logpose.runtime`` logger with a file-only JSON Lines handler, while
Vegapunk's prompt remains the sole owner of the terminal.
"""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

from logpose import RetryPolicy

from .config import Config, config

RuntimeRole = Literal["interactive", "scheduler"]

_LOG_MAX_BYTES = 5 * 1024 * 1024
_LOG_BACKUP_COUNT = 3
_LOGPOSE_EVENT_FIELDS = (
    "schema_version",
    "run_id",
    "provider",
    "model",
    "turn_id",
    "attempt_id",
    "request_id",
    "error_id",
    "tool_call_id",
    "tool_name",
    "duration_seconds",
    "queue_seconds",
    "execution_seconds",
    "retry_delay_seconds",
    "status_code",
    "tool_timed_out",
    "result_bytes",
    "input_tokens",
    "output_tokens",
    "cache_read_input_tokens",
    "cache_creation_input_tokens",
    "exception_type",
    "stop_reason",
    "iterations",
)


def runtime_log_path(cfg: Config, role: RuntimeRole) -> Path:
    """Return the process-specific JSONL file beside the configured database."""
    name = {
        "interactive": "vegapunk-runtime.jsonl",
        "scheduler": "scheduler-runtime.jsonl",
    }[role]
    return cfg.db_file.parent / name


class _JsonlRuntimeFormatter(logging.Formatter):
    """Serialise only Logpose's documented metadata fields from each LogRecord."""

    def format(self, record: logging.LogRecord) -> str:
        # Logpose attaches these fields through RuntimeEvent.log_fields(). Never
        # serialise record.getMessage(): it is not part of the telemetry schema
        # and a future Logpose message must not become a content-leak path.
        row = {
            "event": getattr(record, "logpose_event", None),
            **{
                field: getattr(record, f"logpose_{field}", None)
                for field in _LOGPOSE_EVENT_FIELDS
            },
        }
        return json.dumps(row, separators=(",", ":"), sort_keys=True)


class _QuietRotatingFileHandler(RotatingFileHandler):
    """Keep optional telemetry failures out of the interactive terminal."""

    def handleError(self, record: logging.LogRecord) -> None:  # noqa: N802 - logging API
        # Runtime logging is observability only. The application must continue
        # if a disk fills or a file is removed between rotations.
        return None


def configure_runtime_logging(cfg: Config = config, role: RuntimeRole = "interactive") -> Path:
    """Route Logpose's runtime logger to this process's dedicated JSONL file."""
    path = runtime_log_path(cfg, role)
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("logpose.runtime")
    logger.setLevel(logging.DEBUG)
    # A Logpose runtime record must never appear through Vegapunk's terminal or
    # any future root handler. This handler is its one in-process destination.
    logger.propagate = False

    for handler in tuple(logger.handlers):
        if getattr(handler, "_vegapunk_runtime_handler", False):
            if getattr(handler, "_vegapunk_runtime_path", None) == str(path):
                return path
            logger.removeHandler(handler)
            handler.close()

    handler = _QuietRotatingFileHandler(
        path,
        maxBytes=_LOG_MAX_BYTES,
        backupCount=_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler._vegapunk_runtime_handler = True  # type: ignore[attr-defined]
    handler._vegapunk_runtime_path = str(path)  # type: ignore[attr-defined]
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(_JsonlRuntimeFormatter())
    logger.addHandler(handler)
    return path


def agent_runtime_options(
    cfg: Config = config, role: RuntimeRole = "interactive"
) -> dict[str, object]:
    """Return the Logpose policy shared by interactive and scheduled agents."""
    configure_runtime_logging(cfg, role)
    return {
        "retry_policy": RetryPolicy(max_attempts=cfg.provider_max_attempts),
        "provider_turn_timeout": cfg.provider_turn_timeout,
        "max_concurrent_tools": cfg.max_concurrent_tools,
        "tool_timeout": cfg.tool_timeout,
        "tool_error_mode": "safe",
    }

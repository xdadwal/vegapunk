"""Shared Logpose limits and the deliberately file-only runtime observer."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace

from logpose import Agent

from tests.fake_provider import FakeProvider, backend_for, says
from vegapunk import runtime, scheduler_worker, session
from vegapunk.config import config


def _emit_runtime_event() -> None:
    logging.getLogger("logpose.runtime").debug(
        "never serialise this message",
        extra={
            "logpose_event": "provider.attempt.failed",
            "logpose_schema_version": 1,
            "logpose_run_id": "run-123",
            "logpose_provider": "fake",
            "logpose_model": "fake-model",
            "logpose_attempt_id": "attempt-456",
            "logpose_error_id": "error-789",
            "logpose_status_code": 429,
            "logpose_retry_delay_seconds": 0.5,
            "prompt": "must never reach the runtime file",
        },
    )


def test_runtime_logger_writes_only_content_free_jsonl(tmp_path, capsys):
    cfg = replace(config, db_file=tmp_path / "vegapunk.db")
    path = runtime.configure_runtime_logging(cfg)

    _emit_runtime_event()

    row = json.loads(path.read_text())
    assert row == {
        "attempt_id": "attempt-456",
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
        "duration_seconds": None,
        "error_id": "error-789",
        "exception_type": None,
        "execution_seconds": None,
        "input_tokens": None,
        "iterations": None,
        "model": "fake-model",
        "event": "provider.attempt.failed",
        "output_tokens": None,
        "provider": "fake",
        "queue_seconds": None,
        "request_id": None,
        "result_bytes": None,
        "retry_delay_seconds": 0.5,
        "run_id": "run-123",
        "schema_version": 1,
        "status_code": 429,
        "stop_reason": None,
        "tool_call_id": None,
        "tool_name": None,
        "tool_timed_out": None,
        "turn_id": None,
    }
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_logpose_runtime_events_are_captured_by_standard_logging(tmp_path):
    cfg = replace(config, db_file=tmp_path / "vegapunk.db")
    path = runtime.configure_runtime_logging(cfg)

    result = asyncio.run(Agent(FakeProvider(says("done"))).run("hello"))

    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert result.text == "done"
    assert {row["event"] for row in rows} >= {"run.started", "run.completed"}
    assert logging.getLogger("logpose.runtime").propagate is False


def test_runtime_observer_rotates_files(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_LOG_MAX_BYTES", 1)
    monkeypatch.setattr(runtime, "_LOG_BACKUP_COUNT", 1)
    cfg = replace(config, db_file=tmp_path / "vegapunk.db")
    path = runtime.configure_runtime_logging(cfg)

    _emit_runtime_event()
    _emit_runtime_event()

    assert path.with_suffix(".jsonl.1").exists()


def test_runtime_options_share_limits_but_keep_process_logs_separate(tmp_path):
    cfg = replace(
        config,
        db_file=tmp_path / "vegapunk.db",
        provider_max_attempts=1,
        provider_turn_timeout=None,
        max_concurrent_tools=2,
        tool_timeout=None,
    )

    interactive = runtime.agent_runtime_options(cfg, "interactive")
    scheduler = runtime.agent_runtime_options(cfg, "scheduler")

    for key in (
        "provider_turn_timeout",
        "max_concurrent_tools",
        "tool_timeout",
        "tool_error_mode",
    ):
        assert interactive[key] == scheduler[key]
    assert interactive["retry_policy"].max_attempts == 1
    assert scheduler["retry_policy"].max_attempts == 1
    assert "observers" not in interactive
    assert "observers" not in scheduler
    assert runtime.runtime_log_path(cfg, "interactive").name == "vegapunk-runtime.jsonl"
    assert runtime.runtime_log_path(cfg, "scheduler").name == "scheduler-runtime.jsonl"


def test_session_and_scheduler_apply_the_shared_runtime_options(monkeypatch, tmp_path):
    cfg = replace(config, db_file=tmp_path / "vegapunk.db")
    seen: dict[str, dict] = {}

    class CapturedAgent:
        def __init__(self, *_args, **kwargs):
            seen.setdefault("agents", []).append(kwargs)

    monkeypatch.setattr(session, "config", cfg)
    monkeypatch.setattr(session, "Agent", CapturedAgent)
    monkeypatch.setattr(scheduler_worker, "config", cfg)
    monkeypatch.setattr(scheduler_worker, "Agent", CapturedAgent)

    session.Session(backend_for(), tools=[])
    scheduler_worker.build_agent(backend_for())

    interactive, scheduled = seen["agents"]
    for key in (
        "retry_policy",
        "provider_turn_timeout",
        "max_concurrent_tools",
        "tool_timeout",
        "tool_error_mode",
    ):
        assert interactive[key] == scheduled[key]

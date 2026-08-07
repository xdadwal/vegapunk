"""Shared Logpose limits and the deliberately file-only runtime observer."""

from __future__ import annotations

import json
from dataclasses import replace

from logpose import RuntimeEvent

from tests.fake_provider import backend_for
from vegapunk import runtime, scheduler_worker, session
from vegapunk.config import config


def _event() -> RuntimeEvent:
    return RuntimeEvent(
        name="provider.attempt.failed",
        run_id="run-123",
        provider="fake",
        model="fake-model",
        attempt_id="attempt-456",
        error_id="error-789",
        status_code=429,
        retry_delay_seconds=0.5,
    )


def test_runtime_observer_writes_only_content_free_jsonl(tmp_path, capsys):
    path = tmp_path / "runtime.jsonl"

    runtime.JsonlRuntimeObserver(path)(_event())

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
        "name": "provider.attempt.failed",
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


def test_runtime_observer_rotates_files(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "_LOG_MAX_BYTES", 1)
    monkeypatch.setattr(runtime, "_LOG_BACKUP_COUNT", 1)
    observer = runtime.JsonlRuntimeObserver(tmp_path / "runtime.jsonl")

    observer(_event())
    observer(_event())

    assert (tmp_path / "runtime.jsonl.1").exists()


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
    assert interactive["observers"] != scheduled["observers"]

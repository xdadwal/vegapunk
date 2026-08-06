"""Tests for the scheduler worker process — the backend it builds from config,
its parent-death watchdog, and the startup path in ``main``.

The worker's *loop* is ``Scheduler.serve`` and is covered in test_scheduler.py;
what's pinned here is everything around it that only exists because scheduled
runs moved out of the REPL's process.
"""

from __future__ import annotations

import os
import threading
from dataclasses import replace

import pytest

from vegapunk import scheduler_worker
from vegapunk.backend import current_effort
from vegapunk.config import config
from tests.fake_provider import backend_for


def _claude_backend():
    """What create_backend returns for claude: a backend that takes effort."""
    return backend_for(model_label="fake-claude", effort_key="output_config")


def _local_backend():
    """What create_backend returns for local: no effort setting."""
    return backend_for(model_label="fake-local")


def _capture_create_backend(monkeypatch, backend=None):
    """Record what create_backend was asked for; return the recording dict."""
    seen: dict = {}

    def _fake(provider, cfg=config):
        seen["provider"] = provider
        seen["claude_model"] = cfg.claude_model
        return backend if backend is not None else _local_backend()

    monkeypatch.setattr("vegapunk.scheduler_worker.create_backend", _fake)
    return seen


def test_build_backend_inherits_the_launch_config_by_default(monkeypatch):
    # Unset VEGAPUNK_SCHEDULER_MODEL means "whatever the REPL launched with" —
    # the worker inherits the environment, so this is the zero-config path.
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config",
        replace(config, scheduler_model="", provider="claude", claude_model="sonnet"),
    )
    seen = _capture_create_backend(monkeypatch)

    scheduler_worker.build_backend()

    assert seen["provider"] == "claude"
    assert seen["claude_model"] == "sonnet"


def test_build_backend_prefers_the_scheduler_spec(monkeypatch):
    # The whole point of the variable: keep unattended runs on the free local
    # model while your own turns use a billed provider.
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config",
        replace(config, scheduler_model="local", provider="claude", claude_model="opus"),
    )
    seen = _capture_create_backend(monkeypatch)

    scheduler_worker.build_backend()

    assert seen["provider"] == "local"
    assert seen["claude_model"] == "opus"  # untouched; local ignores it


def test_build_backend_splits_provider_and_model(monkeypatch):
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config",
        replace(config, scheduler_model="claude:opus", provider="local", claude_model=""),
    )
    seen = _capture_create_backend(monkeypatch)

    scheduler_worker.build_backend()

    assert seen["provider"] == "claude"
    assert seen["claude_model"] == "opus"


def test_build_backend_applies_effort_with_fallback(monkeypatch):
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config",
        replace(config, scheduler_model="claude", scheduler_effort="", claude_effort="xhigh"),
    )
    _capture_create_backend(monkeypatch, _claude_backend())

    backend = scheduler_worker.build_backend()

    assert current_effort(backend) == "xhigh"  # fell back to the general claude effort


def test_build_backend_scheduler_effort_wins(monkeypatch):
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config",
        replace(config, scheduler_model="claude", scheduler_effort="low", claude_effort="max"),
    )
    _capture_create_backend(monkeypatch, _claude_backend())

    assert current_effort(scheduler_worker.build_backend()) == "low"


def test_build_backend_says_so_when_effort_cannot_apply(monkeypatch, capsys):
    # Asking for effort on local is a config mismatch — dropping it silently
    # would leave you believing scheduled runs use a setting they can't.
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config",
        replace(config, scheduler_model="local", scheduler_effort="high"),
    )
    _capture_create_backend(monkeypatch, _local_backend())

    scheduler_worker.build_backend()

    assert "ignoring effort 'high'" in capsys.readouterr().err


def test_build_backend_rejects_a_malformed_spec(monkeypatch):
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config", replace(config, scheduler_model="gpt5:turbo")
    )
    with pytest.raises(ValueError, match="Unknown provider"):
        scheduler_worker.build_backend()


def test_watch_parent_stops_when_reparented(monkeypatch):
    # cli terminates the worker on every ordinary exit; this is the kill -9
    # backstop. Being reparented (getppid changes) is the signal.
    stop = threading.Event()
    monkeypatch.setattr(os, "getppid", lambda: 1)  # as if launchd adopted us

    scheduler_worker.watch_parent(stop, parent_pid=4242, interval=0.01)

    assert stop.is_set()


def test_watch_parent_returns_when_stopped_normally(monkeypatch):
    # A normal shutdown sets the same event; the watchdog must not hang on it.
    stop = threading.Event()
    monkeypatch.setattr(os, "getppid", lambda: 4242)  # parent still alive
    watcher = threading.Thread(
        target=scheduler_worker.watch_parent, args=(stop, 4242, 0.01), daemon=True
    )
    watcher.start()
    stop.set()
    watcher.join(timeout=2)

    assert not watcher.is_alive()


def test_main_takes_the_scheduler_lock_not_the_repl_one(monkeypatch):
    # The worker runs alongside a REPL that already holds the process lock, so
    # taking that one would refuse it start. Pin which lock it reaches for.
    taken: list[str] = []
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.db.acquire_scheduler_lock", lambda: taken.append("scheduler")
    )
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.db.acquire_process_lock", lambda: taken.append("repl")
    )
    monkeypatch.setattr("vegapunk.scheduler_worker.build_backend", _local_backend)
    # serve() returns at once so main() doesn't block the test.
    monkeypatch.setattr("vegapunk.scheduler_worker.Scheduler.serve", lambda self: None)

    scheduler_worker.main()

    assert taken == ["scheduler"]


def test_main_exits_cleanly_on_a_bad_model_spec(monkeypatch, capsys):
    # A traceback here would land in the log with the useful line buried; the
    # REPL's "worker exited" note plus this message is the story instead.
    monkeypatch.setattr("vegapunk.scheduler_worker.db.acquire_scheduler_lock", lambda: None)

    def _boom():
        raise ValueError("Unknown provider 'gpt5'")

    monkeypatch.setattr("vegapunk.scheduler_worker.build_backend", _boom)

    with pytest.raises(SystemExit) as exit_info:
        scheduler_worker.main()

    assert exit_info.value.code == 1
    assert "bad model configuration" in capsys.readouterr().err

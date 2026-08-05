"""Tests for the scheduler worker process — the brain it builds from config,
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
from vegapunk.config import config


class _FakeClaudeBrain:
    """A brain that carries an effort setting, like ClaudeBrain — used to check
    effort is applied without importing the SDK."""

    model_label = "fake-claude"

    def __init__(self) -> None:
        self.effort: str | None = None

    def set_effort(self, level: str) -> None:
        self.effort = level


class _FakeLocalBrain:
    """A brain with no effort setting, like DMRBrain."""

    model_label = "fake-local"


def _capture_create_brain(monkeypatch, brain=None):
    """Record what create_brain was asked for; return the recording dict."""
    seen: dict = {}

    def _fake(provider, cfg=config):
        seen["provider"] = provider
        seen["claude_model"] = cfg.claude_model
        return brain if brain is not None else _FakeLocalBrain()

    monkeypatch.setattr("vegapunk.scheduler_worker.create_brain", _fake)
    return seen


def test_build_brain_inherits_the_launch_config_by_default(monkeypatch):
    # Unset VEGAPUNK_SCHEDULER_MODEL means "whatever the REPL launched with" —
    # the worker inherits the environment, so this is the zero-config path.
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config",
        replace(config, scheduler_model="", provider="claude", claude_model="sonnet"),
    )
    seen = _capture_create_brain(monkeypatch)

    scheduler_worker.build_brain()

    assert seen["provider"] == "claude"
    assert seen["claude_model"] == "sonnet"


def test_build_brain_prefers_the_scheduler_spec(monkeypatch):
    # The whole point of the variable: keep unattended runs on the free local
    # model while your own turns use a billed provider.
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config",
        replace(config, scheduler_model="local", provider="claude", claude_model="opus"),
    )
    seen = _capture_create_brain(monkeypatch)

    scheduler_worker.build_brain()

    assert seen["provider"] == "local"
    assert seen["claude_model"] == "opus"  # untouched; local ignores it


def test_build_brain_splits_provider_and_model(monkeypatch):
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config",
        replace(config, scheduler_model="claude:opus", provider="local", claude_model=""),
    )
    seen = _capture_create_brain(monkeypatch)

    scheduler_worker.build_brain()

    assert seen["provider"] == "claude"
    assert seen["claude_model"] == "opus"


def test_build_brain_applies_effort_with_fallback(monkeypatch):
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config",
        replace(config, scheduler_model="claude", scheduler_effort="", claude_effort="xhigh"),
    )
    brain = _FakeClaudeBrain()
    _capture_create_brain(monkeypatch, brain)

    scheduler_worker.build_brain()

    assert brain.effort == "xhigh"  # fell back to the general claude effort


def test_build_brain_scheduler_effort_wins(monkeypatch):
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config",
        replace(config, scheduler_model="claude", scheduler_effort="low", claude_effort="max"),
    )
    brain = _FakeClaudeBrain()
    _capture_create_brain(monkeypatch, brain)

    scheduler_worker.build_brain()

    assert brain.effort == "low"


def test_build_brain_says_so_when_effort_cannot_apply(monkeypatch, capsys):
    # Asking for effort on local is a config mismatch — dropping it silently
    # would leave you believing scheduled runs use a setting they can't.
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config",
        replace(config, scheduler_model="local", scheduler_effort="high"),
    )
    _capture_create_brain(monkeypatch, _FakeLocalBrain())

    scheduler_worker.build_brain()

    assert "ignoring effort 'high'" in capsys.readouterr().err


def test_build_brain_rejects_a_malformed_spec(monkeypatch):
    monkeypatch.setattr(
        "vegapunk.scheduler_worker.config", replace(config, scheduler_model="gpt5:turbo")
    )
    with pytest.raises(ValueError, match="Unknown provider"):
        scheduler_worker.build_brain()


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
    monkeypatch.setattr("vegapunk.scheduler_worker.build_brain", _FakeLocalBrain)
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

    monkeypatch.setattr("vegapunk.scheduler_worker.build_brain", _boom)

    with pytest.raises(SystemExit) as exit_info:
        scheduler_worker.main()

    assert exit_info.value.code == 1
    assert "bad model configuration" in capsys.readouterr().err

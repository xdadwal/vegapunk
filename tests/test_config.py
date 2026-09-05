"""Config: provider-selection fields and their env overrides.

Config field defaults are bound when the module is imported, so env-override
tests reload ``vegapunk.config`` under a patched environment and restore the
pristine module state afterwards (other modules hold their own references to
the original singleton, so the reload is invisible to them).
"""

from __future__ import annotations

import importlib

import pytest

import vegapunk.config as config_module


def _reloaded_config(monkeypatch, **env: str):
    """Reload vegapunk.config with the given env and return a fresh Config."""
    for key in (
        "VEGAPUNK_PROVIDER",
        "VEGAPUNK_CLAUDE_MODEL",
        "VEGAPUNK_CLAUDE_CONTEXT_WINDOW",
        "VEGAPUNK_CLAUDE_EFFORT",
        "VEGAPUNK_MAX_STEPS",
        "VEGAPUNK_PROVIDER_MAX_ATTEMPTS",
        "VEGAPUNK_PROVIDER_TURN_TIMEOUT",
        "VEGAPUNK_MAX_CONCURRENT_TOOLS",
        "VEGAPUNK_TOOL_TIMEOUT",
        "VEGAPUNK_DB_FILE",
        "VEGAPUNK_EMBED_MODEL",
        "VEGAPUNK_MEMORY_ENABLED",
        "VEGAPUNK_MEMORY_MODEL",
        "VEGAPUNK_MEMORY_REVIEW",
        "VEGAPUNK_MEMORY_TIMEOUT",
        "VEGAPUNK_MEMORY_SCAN_INTERVAL",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(config_module).Config()


def _restore(monkeypatch) -> None:
    """Undo env patches and rebuild the module's import-time defaults."""
    monkeypatch.undo()
    importlib.reload(config_module)


def test_provider_defaults_to_local(monkeypatch):
    try:
        cfg = _reloaded_config(monkeypatch)
        assert cfg.provider == "local"
        assert cfg.claude_model == ""
        assert cfg.claude_context_window == 200000
        assert cfg.claude_effort == ""  # "" = the SDK default ("high")
    finally:
        _restore(monkeypatch)


def test_memory_defaults_and_review_override(monkeypatch):
    try:
        cfg = _reloaded_config(monkeypatch)
        assert cfg.memory_enabled and cfg.memory_model == "codex" and cfg.memory_review == "auto"
        assert cfg.memory_timeout == 600
        assert cfg.memory_scan_interval == 3600
        cfg = _reloaded_config(monkeypatch, VEGAPUNK_MEMORY_ENABLED="false",
                               VEGAPUNK_MEMORY_MODEL="claude:haiku", VEGAPUNK_MEMORY_REVIEW="review",
                               VEGAPUNK_MEMORY_TIMEOUT="900", VEGAPUNK_MEMORY_SCAN_INTERVAL="7200")
        assert not cfg.memory_enabled and cfg.memory_model == "claude:haiku" and cfg.memory_review == "review"
        assert cfg.memory_timeout == 900 and cfg.memory_scan_interval == 7200
    finally:
        _restore(monkeypatch)


@pytest.mark.parametrize("name", ["VEGAPUNK_MEMORY_ENABLED", "VEGAPUNK_MEMORY_REVIEW",
                                  "VEGAPUNK_MEMORY_TIMEOUT", "VEGAPUNK_MEMORY_SCAN_INTERVAL"])
def test_memory_invalid_policy_is_rejected(monkeypatch, name):
    try:
        with pytest.raises(ValueError, match=name):
            _reloaded_config(monkeypatch, **{name: "invalid"})
    finally:
        _restore(monkeypatch)


def test_foreground_memory_is_reserved_for_explicit_requests(monkeypatch):
    try:
        prompt = _reloaded_config(monkeypatch).system_prompt
        assert "explicitly asks you to remember" in prompt
        assert "Don't proactively call remember" in prompt
    finally:
        _restore(monkeypatch)


def test_max_steps_defaults_to_a_multi_step_budget(monkeypatch):
    try:
        assert _reloaded_config(monkeypatch).max_steps == 25
    finally:
        _restore(monkeypatch)


def test_system_prompt_scopes_tools_to_tasks_that_need_them(monkeypatch):
    try:
        prompt = _reloaded_config(monkeypatch).system_prompt
        assert "For multi-step tasks, use an agent loop" in prompt
        assert "don't add unnecessary tool calls" in prompt
        assert "Never claim success unless" in prompt
        assert "concise and proportional to the task" in prompt
        assert "a sentence or two" not in prompt
        assert "ask_user is mandatory for EVERY question" in prompt
        assert "recommend a movie based on questions" in prompt
    finally:
        _restore(monkeypatch)


def test_max_steps_env_override(monkeypatch):
    try:
        assert _reloaded_config(monkeypatch, VEGAPUNK_MAX_STEPS="3").max_steps == 3
    finally:
        _restore(monkeypatch)


def test_runtime_policy_defaults_are_safe(monkeypatch):
    try:
        cfg = _reloaded_config(monkeypatch)
        assert cfg.provider_max_attempts == 3
        assert cfg.provider_turn_timeout == "default"
        assert cfg.max_concurrent_tools == 8
        assert cfg.tool_timeout == 300
    finally:
        _restore(monkeypatch)


def test_runtime_policy_env_overrides_support_disabling_timeouts(monkeypatch):
    try:
        cfg = _reloaded_config(
            monkeypatch,
            VEGAPUNK_PROVIDER_MAX_ATTEMPTS="1",
            VEGAPUNK_PROVIDER_TURN_TIMEOUT="0",
            VEGAPUNK_MAX_CONCURRENT_TOOLS="2",
            VEGAPUNK_TOOL_TIMEOUT="0",
        )
        assert cfg.provider_max_attempts == 1
        assert cfg.provider_turn_timeout is None
        assert cfg.max_concurrent_tools == 2
        assert cfg.tool_timeout is None
    finally:
        _restore(monkeypatch)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("VEGAPUNK_PROVIDER_MAX_ATTEMPTS", "0"),
        ("VEGAPUNK_MAX_CONCURRENT_TOOLS", "0"),
        ("VEGAPUNK_PROVIDER_TURN_TIMEOUT", "-1"),
        ("VEGAPUNK_TOOL_TIMEOUT", "-1"),
    ],
)
def test_runtime_policy_rejects_invalid_limits(monkeypatch, name, value):
    try:
        with pytest.raises(ValueError, match=name):
            _reloaded_config(monkeypatch, **{name: value})
    finally:
        _restore(monkeypatch)


def test_embed_model_defaults_to_disabled(monkeypatch):
    try:
        assert _reloaded_config(monkeypatch).embed_model == ""  # "" = embeddings off
    finally:
        _restore(monkeypatch)


def test_db_file_env_override(monkeypatch):
    try:
        cfg = _reloaded_config(monkeypatch, VEGAPUNK_DB_FILE="/tmp/custom/vega.db")
        assert str(cfg.db_file) == "/tmp/custom/vega.db"
    finally:
        _restore(monkeypatch)


def test_embed_model_env_override(monkeypatch):
    try:
        cfg = _reloaded_config(monkeypatch, VEGAPUNK_EMBED_MODEL="ai/qwen3-embedding")
        assert cfg.embed_model == "ai/qwen3-embedding"
    finally:
        _restore(monkeypatch)


def test_provider_env_overrides(monkeypatch):
    try:
        cfg = _reloaded_config(
            monkeypatch,
            VEGAPUNK_PROVIDER="claude",
            VEGAPUNK_CLAUDE_MODEL="opus",
            VEGAPUNK_CLAUDE_CONTEXT_WINDOW="500000",
            VEGAPUNK_CLAUDE_EFFORT="max",
        )
        assert cfg.provider == "claude"
        assert cfg.claude_model == "opus"
        assert cfg.claude_context_window == 500000
        assert cfg.claude_effort == "max"
    finally:
        _restore(monkeypatch)

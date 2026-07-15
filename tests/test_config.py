"""Config: provider-selection fields and their env overrides.

Config field defaults are bound when the module is imported, so env-override
tests reload ``vegapunk.config`` under a patched environment and restore the
pristine module state afterwards (other modules hold their own references to
the original singleton, so the reload is invisible to them).
"""

from __future__ import annotations

import importlib

import vegapunk.config as config_module


def _reloaded_config(monkeypatch, **env: str):
    """Reload vegapunk.config with the given env and return a fresh Config."""
    for key in (
        "VEGAPUNK_PROVIDER",
        "VEGAPUNK_CLAUDE_MODEL",
        "VEGAPUNK_CLAUDE_CONTEXT_WINDOW",
        "VEGAPUNK_CLAUDE_EFFORT",
        "VEGAPUNK_MAX_STEPS",
        "VEGAPUNK_DB_FILE",
        "VEGAPUNK_EMBED_MODEL",
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


def test_max_steps_defaults_to_a_multi_step_budget(monkeypatch):
    try:
        assert _reloaded_config(monkeypatch).max_steps == 25
    finally:
        _restore(monkeypatch)


def test_max_steps_env_override(monkeypatch):
    try:
        assert _reloaded_config(monkeypatch, VEGAPUNK_MAX_STEPS="3").max_steps == 3
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

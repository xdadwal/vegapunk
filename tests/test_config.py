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
    for key in ("VEGAPUNK_PROVIDER", "VEGAPUNK_CLAUDE_MODEL", "VEGAPUNK_CLAUDE_CONTEXT_WINDOW"):
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
    finally:
        _restore(monkeypatch)


def test_provider_env_overrides(monkeypatch):
    try:
        cfg = _reloaded_config(
            monkeypatch,
            VEGAPUNK_PROVIDER="claude",
            VEGAPUNK_CLAUDE_MODEL="opus",
            VEGAPUNK_CLAUDE_CONTEXT_WINDOW="500000",
        )
        assert cfg.provider == "claude"
        assert cfg.claude_model == "opus"
        assert cfg.claude_context_window == 500000
    finally:
        _restore(monkeypatch)

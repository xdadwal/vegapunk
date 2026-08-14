"""Tests for complete, runtime-specific system-prompt composition."""

from __future__ import annotations

import pytest

from vegapunk import prompt
from vegapunk.config import config


@pytest.fixture(autouse=True)
def _fixed_dynamic_stanzas(monkeypatch):
    monkeypatch.setattr(prompt.memory, "as_system_block", lambda: "\n\nMEMORY")
    monkeypatch.setattr(prompt.skills, "as_system_block", lambda: "\n\nSKILLS")


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("manual", "require the user's approval"),
        ("auto", "without per-call approval"),
        ("unattended", "tools that require approval are unavailable"),
    ],
)
def test_system_prompt_describes_the_live_approval_mode(mode, expected):
    composed = prompt.system_prompt(config, mode=mode)

    assert f"Approval mode: {mode}" in composed
    assert expected in composed
    assert composed.endswith("MEMORY\n\nSKILLS")


def test_unattended_prompt_tells_the_agent_not_to_retry_blocked_tools():
    composed = prompt.system_prompt(config, mode="unattended")

    assert "Do not repeatedly request them" in composed
    assert "use available alternatives or report the limitation" in composed

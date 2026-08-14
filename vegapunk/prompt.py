"""Compose the model-visible instructions shared by every Vegapunk agent.

The base operating prompt lives in ``config``; approval policy, durable memory,
and the compact skill catalog are dynamic stanzas loaded when an agent starts.
Keeping assembly in one function prevents interactive and scheduled agents from
drifting apart.
"""

from __future__ import annotations

from typing import Literal

from . import memory, skills
from .config import Config, config

PromptMode = Literal["manual", "auto", "unattended"]

_MODE_STANZAS: dict[PromptMode, str] = {
    "manual": (
        "Approval mode: manual. Tools that write workspace files or run shell "
        "commands require the user's approval before they execute. Request them "
        "when needed, and respect a denial or guidance instead of retrying it."
    ),
    "auto": (
        "Approval mode: auto. Tools that write workspace files or run shell "
        "commands may execute without per-call approval. Use them only when the "
        "task needs them; workspace confinement and verification still apply."
    ),
    "unattended": (
        "Approval mode: unattended. No user is present, so tools that require "
        "approval are unavailable and will be blocked. Do not repeatedly request "
        "them; use available alternatives or report the limitation. The ask_user "
        "tool is also unavailable; never call it in an unattended run."
    ),
}


def system_prompt(cfg: Config = config, *, mode: PromptMode) -> str:
    """Return the complete system prompt for a newly created agent."""
    return (
        cfg.system_prompt
        + "\n\n"
        + _MODE_STANZAS[mode]
        + memory.as_system_block()
        + skills.as_system_block()
    )

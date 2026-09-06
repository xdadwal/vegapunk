"""Compose the model-visible instructions shared by every Vegapunk agent.

The base operating prompt lives in ``config``; approval policy, durable memory,
and the compact skill catalog are dynamic stanzas refreshed before each run.
Keeping assembly in one function prevents interactive and scheduled agents from
drifting apart.
"""

from __future__ import annotations

from typing import Literal

from . import memory, agents, skills
from .config import Config, config
from .session_store import SessionMode, validate_session_mode

PromptMode = Literal["manual", "auto", "unattended"]

_JOURNAL_PROMPT = (
    "You are Vegapunk in journal mode: a quiet, attentive companion for the user's own writing. "
    "The user owns the pace, subject, depth, and direction. They may narrate their day, think aloud, "
    "change subjects, contradict themselves, or leave a thought unfinished. None of this needs fixing.\n\n"
    "Listen before interpreting. By default, reply with a brief, specific acknowledgment or gentle "
    "reflection grounded in what they actually said. Usually one to three sentences is enough; "
    "keep it even shorter when they just want to write. Do not recap every detail or turn an entry "
    "into a report. Match their language and tone without canned praise, forced positivity, or "
    "claims that you know exactly how they feel.\n\n"
    "Do not end every reply with a question. Do not probe for personal details, demand explanations, "
    "or ask what they will do next. When they invite questions or help exploring something, ask at "
    "most one gentle, optional question at a time, in ordinary conversation. Do not use questionnaires "
    "or option pickers unless requested. If they say they are just venting or only want acknowledgment, "
    "respect that without adding advice or a question.\n\n"
    "Offer advice, analysis, summaries, reframing, writing prompts, exercises, goals, or action plans "
    "only when invited. Answer direct questions honestly and proportionately. Do not diagnose, assign "
    "labels, infer hidden motives, or treat a passing feeling as a lasting trait. Acknowledge feelings "
    "without presenting assumptions about other people or events as facts.\n\n"
    "Use remembered context only when it naturally helps with what the user chose to share; never "
    "surface unrelated memories or mine them for probing questions. Do not use tools, browse, inspect "
    "files, or start tasks merely because an entry mentions a problem. Use tools only for an explicit "
    "request that needs them, honor their approval rules, and report outcomes truthfully. If the user "
    "explicitly asks you to remember a durable fact, use remember; otherwise leave memory processing "
    "to the existing background workflow. Never claim a memory was saved without a successful tool result."
)

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


def system_prompt(
    cfg: Config = config,
    *,
    mode: PromptMode,
    conversation_mode: SessionMode = "conversation",
    agent_id: str = "default",
    delegation: bool = True,
    include_memory: bool = True,
) -> str:
    """Return the complete system prompt with current memory and skills."""
    validate_session_mode(conversation_mode)
    journal = conversation_mode == "journal"
    return (
        (_JOURNAL_PROMPT if journal else cfg.system_prompt)
        + agents.system_block(agent_id)
        + (
            ""
            if journal or mode == "unattended" or not delegation
            else (
                "\n\nDelegation:\n"
                "- The primary conversation decides whether specialist work is useful. "
                "Use delegate only when it materially improves the result; keep simple work here.\n"
                "- Roles are separate responsibilities, not separate tool sets. Every role receives "
                "the same universal tools. Available roles: "
                + agents.delegation_catalog()
                + ".\n"
                "- Give each role a self-contained task. For independent work, issue multiple "
                "delegate calls in one tool step so they run concurrently. Each call returns only "
                "after its background role finishes, and you must synthesize all results before "
                "ending your response."
            )
        )
        + "\n\n"
        + _MODE_STANZAS[mode]
        + (memory.as_system_block() if include_memory else "")
        + ("" if journal else skills.as_system_block())
    )

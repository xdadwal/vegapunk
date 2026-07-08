"""The ``use_skill`` tool — pull a skill's full instructions into the turn.

Unguarded: it reads Vegapunk's own skills directory (``.agents/skills/`` by
default), not the user's workspace, so it doesn't go through the approval
gate. The store lives in ``vegapunk/skills.py``; the one-line ads in the
system prompt tell the model what exists, and this tool is how a matching
skill's body actually enters context — the on-demand half of progressive
disclosure. A skill that bundles extra files (``scripts/``, ``references/``,
``assets/`` per the Agent Skills format) gets a pointer to its directory so
the model can resolve those references with its file tools.
"""

from __future__ import annotations

from .. import skills
from ..config import config
from ..skills import SkillNotFound
from .registry import tool


@tool
def use_skill(name: str) -> str:
    """Load the full instructions for one of your skills — the user-written
    guides listed in your system prompt under 'Skills you have'. Call this
    FIRST, before starting any task that matches a listed skill, then follow
    the instructions it returns. Don't guess a skill's steps from its one-line
    description."""
    try:
        skill, body = skills.load_skill(name)
    except SkillNotFound as exc:
        # The exception carries the available names — no second discovery
        # pass, so malformed-file notes print once per call, not twice.
        names = sorted(exc.available)
        if not names:
            return (
                "No skills are installed. Do the task using your own judgment. "
                f"(The user can add skills under {skills.skills_dir()} — one "
                "directory per skill holding a SKILL.md, per the Agent Skills "
                "format: https://agentskills.io.)"
            )
        return (
            f"No skill named {name!r}. Available skills: {', '.join(names)}. "
            "Call use_skill again with one of these exact names, or do the "
            "task without a skill."
        )
    # A skill file is user-authored and unbounded — cap like every other
    # unbounded-content tool, with the house truncation marker.
    if len(body) > config.output_char_cap:
        body = body[: config.output_char_cap] + "\n...[truncated]"
    note = skills.file_reference_note(skill)
    if note:  # after the cap: the pointer to bundled files must never be truncated away
        body = f"{body}\n\n{note}"
    return f"Skill '{skill.name}' loaded. Follow these instructions to do the task:\n\n{body}"

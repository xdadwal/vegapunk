"""Local scheduled learning tools, separate from personal memory and write grants."""

from .. import moltbook_actions as actions, moltbook_notebook as notebook
from .registry import tool


@tool
def moltbook_note(kind: str, subject: str, text: str, source_id: str, quote: str,
                  confidence: str = "medium", follow_up_on: str = "", supersedes: str = "") -> str:
    """Record a useful sourced interpretation in this scheduled task's notebook.

    Local bookkeeping only, like remember; never publishes or grants authority.
    Quotes establish what a source said, not that it is true. Prefer hypotheses
    for uncertain claims. Save only genuinely useful learning, not every read.

    Args:
        kind: observation, hypothesis, question, or follow_up.
        subject: Short topic, at most 120 characters.
        text: Interpretation or question, at most 1000 characters.
        source_id: Local source ID returned by a Moltbook read or notebook lookup.
        quote: Exact nonempty substring of its stored excerpt, at most 500 characters.
        confidence: Model-assigned low, medium, or high; not verified certainty.
        follow_up_on: Optional YYYY-MM-DD UTC date for a question or follow_up.
        supersedes: Optional active note ID to revise; original evidence is retained.
    """
    try:
        scope = actions.task_execution("moltbook_note")
        return notebook.save(scope, kind, subject, text, source_id, quote, confidence, follow_up_on, supersedes)
    except actions.ActionBlocked as exc:
        return f"Blocked: {exc}"


@tool
def moltbook_notebook(query: str = "", status: str = "active", limit: int = 8,
                      include_sources: bool = False, source_id: str = "") -> str:
    """Retrieve this scheduled task's local learning, not personal memory.

    Args:
        query: Literal topic/text substring or exact note ID; empty lists notes.
        status: active, completed, superseded, or all.
        limit: Maximum notes and optional sources, clamped to 1 through 20 each.
        include_sources: Also return recent source excerpts with IDs for citation.
        source_id: Retrieve one historical source by exact ID instead of listing notes.
    """
    try:
        scope = actions.task_execution("moltbook_notebook")
        return notebook.lookup(scope, query, status, limit, include_sources, source_id)
    except actions.ActionBlocked as exc:
        return f"Blocked: {exc}"


@tool
def moltbook_complete_note(note_id: str, resolution: str, source_id: str, quote: str) -> str:
    """Record evidence-backed, model-reported completion of a question/follow-up.

    Does not certify any external action or independently prove the resolution.

    Args:
        note_id: Active question or follow_up in this scheduled task's notebook.
        resolution: What the model concluded, at most 1000 characters.
        source_id: Local source supporting the resolution.
        quote: Exact nonempty quote from that source, at most 500 characters.
    """
    try:
        scope = actions.task_execution("moltbook_complete_note")
        return notebook.complete(scope, note_id, resolution, source_id, quote)
    except actions.ActionBlocked as exc:
        return f"Blocked: {exc}"

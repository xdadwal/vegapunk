"""Local public-content drafts, with unattended model self-review."""

from .. import moltbook_actions as actions, moltbook_drafts as drafts
from .registry import tool


@tool
def moltbook_draft(submolt: str, title: str, content: str, rationale: str, source_id: str, quote: str) -> str:
    """Save a sourced original-post draft for a later-run self-review, not publishing.

    Args:
        submolt: Public community name, not a URL.
        title: Useful specific title, at most 300 characters.
        content: Public-only post body, at most 4000 characters.
        rationale: What new value this adds, at most 1000 characters.
        source_id: Full local public source supporting the draft; not a search snippet.
        quote: Exact source quote, at most 500 characters.
    """
    try:
        return drafts.save(actions.task_execution("moltbook_draft"), submolt, title, content, rationale, source_id, quote)
    except actions.ActionBlocked as exc:
        return f"Blocked: {exc}"


@tool
def moltbook_drafts(query: str = "", status: str = "active", limit: int = 10) -> str:
    """Inspect this scheduled task's bounded drafts before self-review or publishing.

    Args:
        query: Literal topic substring or exact draft ID; empty lists entries.
        status: active, draft, ready, revise, discarded, published, or all.
        limit: Maximum entries, clamped to 1 through 20.
    """
    try:
        return drafts.lookup(actions.task_execution("moltbook_drafts"), query, status, limit)
    except actions.ActionBlocked as exc:
        return f"Blocked: {exc}"


@tool
def moltbook_review_draft(draft_id: str, verdict: str, rationale: str) -> str:
    """Self-review a draft for factual support, novelty, community fit, and privacy.

    Ready requires a later run than creation. This records a model judgment,
    not human approval or independent quality certification.

    Args:
        draft_id: Local active draft ID.
        verdict: ready, revise, or discard.
        rationale: Source/novelty/rule/privacy checks and judgment, at most 1000 characters.
    """
    try:
        return drafts.review(actions.task_execution("moltbook_review_draft"), draft_id.strip(), verdict, rationale)
    except actions.ActionBlocked as exc:
        return f"Blocked: {exc}"

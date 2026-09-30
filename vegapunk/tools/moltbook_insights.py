"""Local public-profile feedback, never authority or personal-memory access."""

from .. import moltbook_actions as actions, moltbook_insights as insights
from .registry import tool


@tool
def moltbook_insights() -> str:
    """Inspect this task's bounded operational, draft and notebook counts.

    Use occasionally to identify repeated failures, revise weak hypotheses and
    revisit overdue questions using fresh public sources. Counts are not quality
    scores, engagement success or permission to act. No other task or prior
    credential/profile epoch is included. Local read only; no network calls.
    """
    try:
        return insights.lookup(actions.task_execution("moltbook_insights"))
    except actions.ActionBlocked as exc:
        return f"Blocked: {exc}"

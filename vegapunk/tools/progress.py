"""Progress tools bound to a session by the application, never model arguments."""

import json
from dataclasses import replace
from typing import Callable

from .. import progress
from .registry import tool


@tool
def create_plan(title: str, steps: list[str]) -> str:
    """Create a sequential execution plan for substantial work.

    Args:
        title: The outcome to achieve.
        steps: Ordered concrete steps; each depends on previous steps.
    """
    return "Progress tools require a persistent primary session."


@tool
def get_plan() -> str:
    """Read the current session plan and its latest revision before updating it."""
    return "Progress tools require a persistent primary session."


@tool
def update_plan_step(plan_id: str, step: int, status: str, expected_revision: int,
                     outcome: str = "", evidence: list[str] | None = None) -> str:
    """Record a step transition and create an immutable checkpoint.

    Args:
        plan_id: ID from get_plan.
        step: Zero-based step position.
        status: in_progress, completed, blocked, or cancelled.
        expected_revision: Revision returned by get_plan; stale writes are rejected.
        outcome: Observed result; required for completion.
        evidence: Source references supporting the result; required for completion.
    """
    return "Progress tools require a persistent primary session."


def bind(tools: list, identity: Callable[[], str | None]) -> list:
    """Bind only progress tools; preserve all other ToolDefs and their schemas."""
    def handler(name: str):
        def invoke(**arguments):
            sid = identity()
            if sid is None:
                return "Progress tools require a persistent primary session."
            if name == "create_plan":
                result = progress.create(sid, **arguments)
            elif name == "get_plan":
                result = progress.get(sid)
            else:
                result = progress.update(sid, **arguments)
            return json.dumps(result, ensure_ascii=False)
        return invoke

    names = {"create_plan", "get_plan", "update_plan_step"}
    return [replace(t, handler=handler(t.name)) if t.name in names else t for t in tools]

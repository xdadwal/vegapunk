"""Progress tools bound to a session by the application, never model arguments."""

import json
from dataclasses import replace
from typing import Callable

from .. import progress
from .registry import tool


@tool
def create_plan(title: str, steps: list[str], dependencies: list[list[int]] | None = None, operation_id: str = "") -> str:
    """Create a sequential execution plan for substantial work.

    Args:
        title: The outcome to achieve.
        steps: Ordered concrete steps; each depends on previous steps.
        dependencies: Optional dependency indices per step; empty lists allow parallel work.
        operation_id: Stable unique key reused for retries of this exact creation.
    """
    return "Progress tools require a persistent primary session."


@tool
def get_plan(plan_id: str = "") -> str:
    """Read a session plan and its latest revision before updating it.

    Args:
        plan_id: Optional archived plan ID; empty selects the active plan.
    """
    return "Progress tools require a persistent primary session."


@tool
def update_plan_step(plan_id: str, step: int, status: str, expected_revision: int,
                     outcome: str = "", evidence: list[str] | None = None, operation_id: str = "") -> str:
    """Record a step transition and create an immutable checkpoint.

    Args:
        plan_id: ID from get_plan.
        step: Zero-based step position.
        status: in_progress, completed, blocked, or cancelled.
        expected_revision: Revision returned by get_plan; stale writes are rejected.
        outcome: Observed result; required for completion.
        evidence: Source references supporting the result; required for completion.
        operation_id: Stable unique key reused for retries of this exact update.
    """
    return "Progress tools require a persistent primary session."


@tool
def revise_plan(plan_id: str, expected_revision: int, reason: str,
                reopen: list[int] | None = None, dependencies: list[list[int]] | None = None,
                add_steps: list[str] | None = None, operation_id: str = "") -> str:
    """Revise a plan explicitly while preserving completed work and checkpoints.

    Args:
        plan_id: Existing session plan ID.
        expected_revision: Current revision from get_plan.
        reason: Why work changed.
        reopen: Terminal step indices to return to pending, leaves before ancestors.
        dependencies: Optional full dependency graph, editable for pending steps only.
        add_steps: Optional titles to append to the plan.
        operation_id: Stable unique key reused for retries of this exact revision.
    """
    return "Progress tools require a persistent primary session."


def bind(tools: list, identity: Callable[[], str | None], assigned_plan: str = "", assigned_step: int | None = None) -> list:
    """Bind only progress tools; preserve all other ToolDefs and their schemas."""
    def handler(name: str):
        def invoke(**arguments):
            sid = identity()
            if sid is None:
                return "Progress tools require a persistent primary session."
            if assigned_plan and name not in {"get_plan", "update_plan_step"}:
                raise ValueError("Only the primary agent can revise plans")
            if assigned_plan and name == "update_plan_step" and (arguments.get("plan_id") != assigned_plan or arguments.get("step") != assigned_step):
                raise ValueError("Delegate may update only its assigned step")
            if name == "create_plan":
                result = progress.create(sid, **arguments)
            elif name == "get_plan":
                result = progress.get(sid, **arguments)
            elif name == "revise_plan":
                result = progress.revise(sid, **arguments)
            else:
                result = progress.update(sid, **arguments)
            return json.dumps(result, ensure_ascii=False)
        return invoke

    names = {"create_plan", "get_plan", "update_plan_step", "revise_plan"}
    return [replace(t, handler=handler(t.name)) if t.name in names else t for t in tools]

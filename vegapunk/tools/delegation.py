"""Role delegation exposed to the primary model."""

from __future__ import annotations

from .. import delegation
from .registry import tool


@tool
def delegate(role: str, task: str, plan_id: str = "", step: int | None = None) -> str:
    """Run a bounded specialist role in the background and wait for its result.

    Use when a distinct specialist perspective or parallel independent work will
    materially improve the answer. The primary conversation owns delegation and
    must synthesize the returned result. For independent tasks, request multiple
    delegate calls in the same tool step so they run concurrently. Available
    roles: shaka for planning/review, lilith for implementation, edison for
    ideation/experiments, pythagoras for research/synthesis, atlas for debugging,
    and york for simplification/optimization. Do not delegate simple work that
    the primary conversation can complete directly.

    Args:
        role: Specialist role name: shaka, lilith, edison, pythagoras, atlas, or york.
        task: Self-contained assignment including all context the specialist needs.
        plan_id: Optional plan ID for assigned progress updates.
        step: Optional zero-based step, already in progress; provide with plan_id.
    """
    return delegation.run(role, task, plan_id, step) if plan_id or step is not None else delegation.run(role, task)

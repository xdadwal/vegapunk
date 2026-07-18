"""Scheduling tool — let Vegapunk schedule a prompt to run itself on an interval.

Unguarded like the memory tools: it writes a row to Vegapunk's own
``scheduled_tasks`` table in the embedded database, not the user's workspace, so
it doesn't go through the approval gate that file/shell tools do. Scheduling a
prompt can never gain more reach than the prompt itself would have with no human
present — the run it sets up is fail-closed (guarded tools like write_file and
run_shell are blocked unattended). The store and the runner live in
``vegapunk/scheduler.py``.
"""

from __future__ import annotations

from ..scheduler import add_task
from .registry import tool


@tool
def schedule_task(prompt: str, interval_seconds: int) -> str:
    """Schedule a prompt to run automatically every ``interval_seconds`` seconds.

    Use this when the user wants something done repeatedly on a timer — poll a
    web page for changes, periodically save a fact, check something on a cadence.
    ``prompt`` runs as if the user typed it, so write it as a self-contained
    instruction (e.g. "fetch https://example.com and remember any headline that
    changed since last time"). The first run happens one interval from now, not
    immediately.

    The scheduled run is unattended, so it can only use read-only tools like
    fetch_url, search_web, recall, and remember — tools that write files or run
    shell commands are blocked when no human is present to approve them. Tell the
    user that instead of scheduling something that would need those."""
    return add_task(prompt, interval_seconds)

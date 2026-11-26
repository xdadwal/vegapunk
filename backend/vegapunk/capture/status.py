from typing import Literal

Status = Literal["online", "degraded", "offline", "unknown"]


def container_status(state: str, health: str | None) -> Status:
    """The one place Docker's states become the four statuses the UI shows."""
    if state == "running":
        return "degraded" if health == "unhealthy" else "online"
    if state in ("restarting", "paused"):
        return "degraded"
    if state in ("exited", "dead"):
        return "offline"
    return "unknown"

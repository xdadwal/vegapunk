"""Task-local pre-clipping evidence, persisted only by the lifecycle callback."""

from contextvars import ContextVar
from dataclasses import dataclass

LIMIT = 16 * 1024 * 1024


@dataclass
class CapturedOutput:
    text: str | None = None
    complete: bool = True


_current: ContextVar[CapturedOutput | None] = ContextVar("tool_output_capture", default=None)


def begin() -> None:
    """Install a new state object; to_thread copies its task-local reference."""
    _current.set(CapturedOutput())


def record(text: str, *, complete: bool = True) -> None:
    """Retain a bounded text prefix before an instrumented tool clips its preview."""
    state = _current.get()
    if state is None:
        return
    raw = text.encode("utf-8")
    state.text = raw[:LIMIT].decode("utf-8", errors="ignore")
    state.complete = complete and len(raw) <= LIMIT


def finish() -> CapturedOutput | None:
    """Detach the current task's capture for outcome persistence."""
    state = _current.get()
    _current.set(None)
    return state if state is not None and state.text is not None else None

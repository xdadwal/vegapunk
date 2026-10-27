"""Coordinate the single terminal across concurrent agent interactions."""

from collections.abc import Callable, Iterator
from functools import wraps
from typing import ParamSpec, TypeVar, cast
from contextlib import contextmanager
from contextvars import ContextVar
from threading import RLock

LOCK = RLock()
ACTOR: ContextVar[str] = ContextVar("terminal_actor", default="Vegapunk")
_pause: Callable[[], None] | None = None
_input_runner: Callable[[Callable[[], object]], object] | None = None
P = ParamSpec("P")
T = TypeVar("T")


def set_input_runner(runner: Callable[[Callable[[], object]], object] | None) -> None:
    """Register the active composer's UI-loop handoff, not under the UI lock."""
    global _input_runner
    _input_runner = runner


def composer_active() -> bool:
    return _input_runner is not None


def input_call(callback: Callable[[], T]) -> T:
    """Suspend the composer before acquiring any interactive terminal locks."""
    runner = _input_runner
    return callback() if runner is None else cast(T, runner(callback))


def set_pause_handler(handler: Callable[[], None] | None) -> None:
    """Install the CLI activity display's handoff hook."""
    global _pause
    with LOCK:
        _pause = handler


@contextmanager
def interaction() -> Iterator[None]:
    """Serialize input and give a live display back its cursor first."""
    with LOCK:
        if _pause is not None:
            _pause()
        yield


@contextmanager
def actor(name: str) -> Iterator[None]:
    """Label a request without adding display metadata to tool arguments."""
    token = ACTOR.set(name)
    try:
        yield
    finally:
        ACTOR.reset(token)


def coordinated(function: Callable[P, T]) -> Callable[P, T]:
    """Let primary output and terminal input share the activity panel's cursor."""
    @wraps(function)
    def wrapped(*args: P.args, **kwargs: P.kwargs) -> T:
        with interaction():
            return function(*args, **kwargs)
    return wrapped

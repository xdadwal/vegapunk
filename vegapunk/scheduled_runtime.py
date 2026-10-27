"""Persistent provider-loop ownership and optional whole-run deadlines."""

import asyncio
import math
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from weakref import WeakKeyDictionary

from logpose import Agent, Conversation, Event, Message

_RUNTIMES: WeakKeyDictionary[Agent, Agent] = WeakKeyDictionary()
_SELECTED: ContextVar[tuple[Agent, float | None] | None] = ContextVar("scheduled_execution", default=None)


class _ScheduledRuntime(Agent):
    """Keep the scheduler's exclusively owned provider on one sync loop."""

    def stream(self, prompt: str | Message | None = None, *,
               conversation: Conversation | None = None) -> AsyncIterator[Event]:
        selected = _SELECTED.get()
        if selected is None or selected[0].provider is not self.provider:
            raise RuntimeError("scheduled execution context is missing")
        agent, deadline = selected
        events = agent.stream(prompt, conversation=conversation)
        if deadline is None:
            return events

        async def bounded() -> AsyncIterator[Event]:
            try:
                while True:
                    seconds = deadline - time.monotonic()
                    if seconds <= 0:
                        raise TimeoutError("scheduled run deadline expired")
                    # stream_sync consumes each event in a separate async task.
                    # Never span a yield with an asyncio timeout context.
                    async with asyncio.timeout(seconds):
                        try:
                            event = await anext(events)
                        except StopAsyncIteration:
                            return
                    yield event
            except TimeoutError:
                raise TimeoutError("scheduled run deadline expired") from None
            finally:
                await events.aclose()

        return bounded()

    async def aclose(self) -> None:
        closer = getattr(self.provider, "aclose", None)
        if closer is not None:
            await closer()


@contextmanager
def scheduled_runtime(agent: Agent, *, timeout_seconds: float | None = None) -> Iterator[Agent]:
    """Run on a persistent provider loop, optionally bounding the whole run.

    No deadline is added to ordinary schedules. Callers that opt into one share
    it across all model turns and tools, rather than resetting it per event.
    """
    if timeout_seconds is not None and (not math.isfinite(timeout_seconds) or timeout_seconds < 0):
        raise ValueError("run timeout must be finite and non-negative")
    runtime = _RUNTIMES.get(agent)
    if runtime is None:
        runtime = _ScheduledRuntime(agent.provider)
        _RUNTIMES[agent] = runtime
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    token = _SELECTED.set((agent, deadline))
    try:
        yield runtime
    finally:
        _SELECTED.reset(token)


def close_scheduled_agent(agent: Agent) -> None:
    """Close the provider on its owning loop once, at scheduler shutdown."""
    from logpose import close_sync

    runtime = _RUNTIMES.pop(agent, None)
    if runtime is not None:
        close_sync(runtime)

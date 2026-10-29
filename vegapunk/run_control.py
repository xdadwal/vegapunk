"""Thread-safe cancellation of an agent stream on its owning event loop."""

import asyncio
from collections.abc import AsyncIterator
from threading import Event as ThreadEvent, Lock

from logpose import Event


class RunCancelled(Exception):
    """An explicit stop request, distinct from a provider or tool failure."""


def wait_for_cleanup(finished: ThreadEvent) -> None:
    """Repeated Ctrl-C must not detach a host operation already being drained."""
    while not finished.is_set():
        try:
            finished.wait()
        except KeyboardInterrupt:
            continue


class RunControl:
    def __init__(self) -> None:
        self._requested = ThreadEvent()
        self._lock = Lock()
        self._active: tuple[asyncio.AbstractEventLoop, asyncio.Task] | None = None

    def reset(self) -> None:
        with self._lock:
            self._requested.clear()

    def check(self) -> None:
        if self._requested.is_set():
            raise RunCancelled("run cancelled")

    def cancel(self) -> None:
        with self._lock:
            if self._requested.is_set():
                return
            self._requested.set()
            active = self._active
        if active is not None:
            loop, task = active
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                if not loop.is_closed():
                    raise

    def wait_for_stop(self) -> None:
        """Join a cancelled stream task before a synchronous UI resumes."""
        with self._lock:
            active = self._active
        if active is None:
            return
        loop, task = active
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if current_loop is loop:
            raise RuntimeError("Cannot synchronously join the owning agent loop.")
        finished = ThreadEvent()
        try:
            loop.call_soon_threadsafe(task.add_done_callback, lambda _: finished.set())
        except RuntimeError:
            if loop.is_closed():
                return
            raise
        wait_for_cleanup(finished)

    async def stream(self, events: AsyncIterator[Event]) -> AsyncIterator[Event]:
        try:
            while True:
                with self._lock:
                    self._active = (asyncio.get_running_loop(), asyncio.current_task())
                try:
                    self.check()
                    event = await anext(events)
                except StopAsyncIteration:
                    return
                except asyncio.CancelledError:
                    if self._requested.is_set():
                        raise RunCancelled("run cancelled") from None
                    raise
                finally:
                    with self._lock:
                        self._active = None
                yield event
        finally:
            await events.aclose()

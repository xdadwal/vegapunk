"""Keep synchronous tool effects joined through deadlines and cancellation."""

import asyncio
from dataclasses import replace

from logpose import ToolDef


def joined_tool(tool: ToolDef) -> ToolDef:
    """A deadline must not abandon a sync handler still mutating host state."""
    if tool.is_async:
        return tool

    async def invoke(*args: object, **kwargs: object) -> object:
        work = asyncio.create_task(asyncio.to_thread(tool.handler, *args, **kwargs))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError as cancelled:
            # Both a deadline and a later UI interrupt can cancel this await.
            # Neither is allowed to detach the still-running thread.
            while True:
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    if work.done():
                        break
                    continue
                except Exception as error:
                    raise cancelled from error
                break
            raise cancelled

    return replace(tool, handler=invoke, is_async=True)

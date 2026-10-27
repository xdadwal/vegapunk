"""Bounded background role execution for the primary conversation.

The model sees one blocking ``delegate`` tool. Logpose executes synchronous
tool handlers on worker threads and joins every call in a tool batch before the
primary agent can continue, so parallel role work cannot outlive the reply that
requested it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import replace
from threading import Lock
from typing import Protocol

from logpose import (Agent, Conversation, Event, Message, RunEnd, ToolCall, ToolDef,
                     ToolResult, TurnEnd, close_sync, run_sync)

from . import agents, prompt
from .backend import Backend, spawn_backend
from .config import Config, config
from .runtime import agent_runtime_options
from .approval import Approver, Decision
from .delegate_activity import DelegateActivity
from . import terminal

UNAVAILABLE = (
    "Delegation is unavailable outside the primary interactive conversation. "
    "Complete the task directly."
)

_DELEGATE_PROMPT = (
    "\n\nYou are a bounded background specialist reporting to the primary Vegapunk "
    "conversation. Work only on the delegated task. You have no access to the primary "
    "conversation beyond the task text, so do not infer missing context. Use tools when "
    "evidence is needed. Guarded actions follow the primary session's approval policy; "
    "the shared UI obtains any required approval. Do not ask questions or delegate further. "
    "Return concise findings, evidence, caveats, and any useful next action to the primary "
    "agent. Do not address the user or claim the overall request is complete."
)


class Runner(Protocol):
    """Something able to execute one delegated role to completion."""

    def run(self, role: str, task: str) -> str: ...


class _DelegatedAgent(Agent):
    """An agent that owns the separately spawned provider handed to it."""

    def __init__(self, *args, activity: DelegateActivity | None = None,
                 activity_id: str = "", **kwargs) -> None:
        kwargs["tools"] = [_joined_tool(tool) for tool in kwargs.get("tools", ())]
        super().__init__(*args, **kwargs)
        self._activity = activity
        self._activity_id = activity_id

    async def stream(self, prompt: str | Message | None = None, *,
                     conversation: Conversation | None = None) -> AsyncIterator[Event]:
        events = super().stream(prompt, conversation=conversation)
        try:
            async for event in events:
                if self._activity is not None:
                    if isinstance(event, TurnEnd):
                        self._activity.usage(self._activity_id, event)
                    elif isinstance(event, ToolCall):
                        self._activity.status(self._activity_id, f"tool: {event.name}")
                    elif isinstance(event, ToolResult):
                        self._activity.status(self._activity_id, "running")
                    elif isinstance(event, RunEnd) and event.result.stop_reason != "end_turn":
                        self._activity.status(self._activity_id, f"stopped: {event.result.stop_reason}")
                yield event
        finally:
            await events.aclose()

    async def aclose(self) -> None:
        # Logpose leaves caller-built providers open. This provider was created
        # solely for the delegated agent, so close it on the agent's private
        # loop before close_sync stops that loop.
        closer = getattr(self.provider, "aclose", None)
        if closer is not None:
            await closer()


def _joined_tool(tool: ToolDef) -> ToolDef:
    """A deadline must not abandon a sync handler still mutating host state."""
    if tool.is_async:
        return tool

    async def invoke(*args: object, **kwargs: object) -> object:
        work = asyncio.create_task(asyncio.to_thread(tool.handler, *args, **kwargs))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            # wait_for waits for this cleanup before returning its timeout.
            # The original cancellation wins even if the handler itself fails.
            try:
                await work
            finally:
                raise

    return replace(tool, handler=invoke, is_async=True)


class RoleRunner:
    """Run role-isolated agents using the live backend and universal tool set."""

    def __init__(
        self,
        backend: Callable[[], Backend],
        tools: Sequence[ToolDef],
        cfg: Config = config,
        *,
        approver: Approver | None = None,
        approval_mode: Callable[[], str] | None = None,
        activity: DelegateActivity | None = None,
    ) -> None:
        self._backend = backend
        self._tools = tools
        self._config = cfg
        self._approver = approver
        self._approval_mode = approval_mode
        self._activity = activity

    def run(self, role: str, task: str) -> str:
        # Imported lazily so registering the delegate tool can finish while the
        # approval gate itself is importing the tool registry.
        from .gate import make_gate

        agent_id, definition = agents.get_delegate(role)
        assigned = task.strip()
        if not assigned:
            raise ValueError("Delegated task must not be empty.")

        primary = self._backend()
        activity_id = self._activity.start(definition.name, primary.model_label) if self._activity else ""
        approver = (_RoleApprover(self._approver, definition.name, self._activity, activity_id)
                    if self._approver is not None else None)
        mode = self._approval_mode() if self._approval_mode is not None else "manual"
        if self._approver is None:
            mode = "unattended"
        status = "failed"
        worker = None
        backend = None
        try:
            system = prompt.system_prompt(
                self._config, mode=mode, agent_id=agent_id,
                delegation=False, include_memory=False,
            ) + _DELEGATE_PROMPT
            # Resolve policy/logging before allocating a separately owned client.
            runtime = agent_runtime_options(self._config, "interactive")
            backend = spawn_backend(primary)
            worker = _DelegatedAgent(
                backend.provider, system=system, tools=self._tools,
                max_iterations=self._config.max_steps, extra=backend.extra,
                on_tool_call=make_gate(approver, allow_questions=False, allow_delegation=False),
                activity=self._activity, activity_id=activity_id, **runtime,
            )
            outcome = run_sync(worker, assigned)
            result = outcome.text.strip()
            reason = getattr(outcome, "stop_reason", "end_turn")
            status = "completed" if reason == "end_turn" else f"stopped: {reason}"
        finally:
            try:
                if worker is not None:
                    close_sync(worker)
                elif backend is not None:
                    # Construction failed before any request/loop owned this client.
                    closer = getattr(backend.provider, "aclose", None)
                    if closer is not None:
                        asyncio.run(closer())
            except BaseException:
                status = "failed"
                raise
            finally:
                if self._activity:
                    self._activity.finish(activity_id, status)
        if not result:
            result = "(The role returned no text.)"
        return f"{definition.name} role result:\n{result}"


class _RoleApprover(Approver):
    def __init__(self, parent: Approver, role: str,
                 activity: DelegateActivity | None, key: str) -> None:
        self.parent, self.role, self.activity, self.key = parent, role, activity, key

    def approve(self, tool_name: str, arguments: dict) -> Decision:
        if self.activity:
            self.activity.status(self.key, f"approval: {tool_name}")
        try:
            with terminal.actor(f"{self.role} #{self.key}" if self.key else self.role):
                return self.parent.approve(tool_name, arguments)
        finally:
            if self.activity:
                self.activity.status(self.key, f"tool: {tool_name}")


_runner: Runner | None = None
_runner_lock = Lock()


def set_runner(runner: Runner | None) -> None:
    """Install the primary conversation's runner, or clear it during teardown."""
    global _runner
    with _runner_lock:
        _runner = runner


def run(role: str, task: str) -> str:
    """Run through the currently installed primary-conversation coordinator."""
    with _runner_lock:
        runner = _runner
    if runner is None:
        return UNAVAILABLE
    return runner.run(role, task)

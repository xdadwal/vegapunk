"""Bounded background role execution for the primary conversation.

The model sees one blocking ``delegate`` tool. Logpose executes synchronous
tool handlers on worker threads and joins every call in a tool batch before the
primary agent can continue, so parallel role work cannot outlive the reply that
requested it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from threading import Event as ThreadEvent, Lock
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
from .tool_runtime import joined_tool
from .run_control import RunControl, RunCancelled, wait_for_cleanup

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

    def run(self, role: str, task: str, plan_id: str = "", step: int | None = None) -> str: ...


class _DelegatedAgent(Agent):
    """An agent that owns the separately spawned provider handed to it."""

    def __init__(self, *args, activity: DelegateActivity | None = None,
                 activity_id: str = "", control: RunControl | None = None, **kwargs) -> None:
        kwargs["tools"] = [joined_tool(tool) for tool in kwargs.get("tools", ())]
        super().__init__(*args, **kwargs)
        self._activity = activity
        self._activity_id = activity_id
        self._control = control or RunControl()

    async def stream(self, prompt: str | Message | None = None, *,
                     conversation: Conversation | None = None) -> AsyncIterator[Event]:
        events = self._control.stream(super().stream(prompt, conversation=conversation))
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
        session_identity: Callable[[], str | None] | None = None,
    ) -> None:
        self._backend = backend
        self._session_identity = session_identity
        self._tools = tools
        self._config = cfg
        self._approver = approver
        self._approval_mode = approval_mode
        self._activity = activity or DelegateActivity()
        self._control_lock = Lock()
        self._controls: dict[str, RunControl] = {}
        self._stopped = False
        self._idle = ThreadEvent()
        self._idle.set()

    def prepare_turn(self) -> None:
        """Allow new delegates only once the previous turn is fully drained."""
        with self._control_lock:
            if self._controls:
                raise RuntimeError("Cannot begin a turn while sub-agents are still running.")
            self._stopped = False

    def stop_all(self) -> None:
        """Stop inference and reject late siblings; existing sync effects drain."""
        with self._control_lock:
            self._stopped = True
            controls = tuple(self._controls.items())
        if self._approver is not None:
            self._approver.cancel_pending()
        for key, control in controls:
            self._activity.status(key, "stopping")
            control.cancel()

    def wait_for_stop(self) -> None:
        """Join even roles executing between parent stream event pulls."""
        wait_for_cleanup(self._idle)

    def run(self, role: str, task: str, plan_id: str = "", step: int | None = None) -> str:
        # Imported lazily so registering the delegate tool can finish while the
        # approval gate itself is importing the tool registry.
        from .gate import ApprovalCancelled, make_gate
        from . import progress
        from .tools.progress import bind

        sid = self._session_identity() if self._session_identity else None
        if bool(plan_id) != (step is not None):
            raise ValueError("Provide both plan_id and step")
        tools = self._tools
        if plan_id:
            plan = progress.get(sid, plan_id) if sid else None
            if plan is None or isinstance(step, bool) or step < 0 or step >= len(plan["steps"]):
                raise ValueError("Unknown assigned plan step")
            if plan["steps"][step]["status"] != "in_progress":
                raise ValueError("Start the assigned step before delegation")
            tools = bind(list(tools), lambda: sid, plan_id, step)

        agent_id, definition = agents.get_delegate(role)
        assigned = task.strip()
        if not assigned:
            raise ValueError("Delegated task must not be empty.")

        activity_id = self._activity.start(definition.name, "starting")
        control = RunControl()
        with self._control_lock:
            if self._stopped:
                control.cancel()
            self._controls[activity_id] = control
            self._idle.clear()
        status = "failed"
        worker = None
        backend = None
        claimed = False
        try:
            if plan_id:
                progress.claim(sid, plan_id, step, activity_id)
                claimed = True
            control.check()
            approver = (_RoleApprover(self._approver, definition.name, self._activity, activity_id)
                        if self._approver is not None else None)
            mode = self._approval_mode() if self._approval_mode is not None else "manual"
            if self._approver is None:
                mode = "unattended"
            primary = self._backend()
            self._activity.model(activity_id, primary.model_label)
            system = prompt.system_prompt(
                self._config, mode=mode, agent_id=agent_id,
                delegation=False, include_memory=False,
            ) + _DELEGATE_PROMPT
            # Resolve policy/logging before allocating a separately owned client.
            runtime = agent_runtime_options(self._config, "interactive")
            backend = spawn_backend(primary)
            control.check()
            worker = _DelegatedAgent(
                backend.provider, system=system, tools=tools,
                max_iterations=self._config.max_steps, extra=backend.extra,
                on_tool_call=make_gate(approver, allow_questions=False, allow_delegation=False),
                activity=self._activity, activity_id=activity_id, control=control, **runtime,
            )
            outcome = run_sync(worker, assigned)
            result = outcome.text.strip()
            reason = getattr(outcome, "stop_reason", "end_turn")
            status = "completed" if reason == "end_turn" else f"stopped: {reason}"
        except (RunCancelled, ApprovalCancelled):
            status = "cancelled"
            result = "Cancelled by the user. Do not restart this delegated task."
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
                with self._control_lock:
                    self._controls.pop(activity_id, None)
                    if not self._controls:
                        self._idle.set()
                if claimed:
                    progress.release(plan_id, step, activity_id, status)
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


def run(role: str, task: str, plan_id: str = "", step: int | None = None) -> str:
    """Run through the currently installed primary-conversation coordinator."""
    with _runner_lock:
        runner = _runner
    if runner is None:
        return UNAVAILABLE
    return runner.run(role, task, plan_id, step) if plan_id or step is not None else runner.run(role, task)


def stop_all() -> None:
    """Stop the active primary conversation's roles, when it supports stopping."""
    with _runner_lock:
        runner = _runner
    stop = getattr(runner, "stop_all", None)
    if stop is not None:
        stop()


def prepare_turn() -> None:
    """Reset the delegation stop latch at a new primary turn boundary."""
    with _runner_lock:
        runner = _runner
    prepare = getattr(runner, "prepare_turn", None)
    if prepare is not None:
        prepare()


def wait_for_stop() -> None:
    with _runner_lock:
        runner = _runner
    wait = getattr(runner, "wait_for_stop", None)
    if wait is not None:
        wait()

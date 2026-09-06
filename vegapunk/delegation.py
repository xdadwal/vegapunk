"""Bounded background role execution for the primary conversation.

The model sees one blocking ``delegate`` tool. Logpose executes synchronous
tool handlers on worker threads and joins every call in a tool batch before the
primary agent can continue, so parallel role work cannot outlive the reply that
requested it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from threading import Lock
from typing import Protocol

from logpose import Agent, ToolDef, close_sync, run_sync

from . import agents, prompt
from .backend import Backend, spawn_backend
from .config import Config, config
from .runtime import agent_runtime_options

UNAVAILABLE = (
    "Delegation is unavailable outside the primary interactive conversation. "
    "Complete the task directly."
)

_DELEGATE_PROMPT = (
    "\n\nYou are a bounded background specialist reporting to the primary Vegapunk "
    "conversation. Work only on the delegated task. You have no access to the primary "
    "conversation beyond the task text, so do not infer missing context. Use tools when "
    "evidence is needed, but do not ask the user, request approval, or delegate further. "
    "Return concise findings, evidence, caveats, and any useful next action to the primary "
    "agent. Do not address the user or claim the overall request is complete."
)


class Runner(Protocol):
    """Something able to execute one delegated role to completion."""

    def run(self, role: str, task: str) -> str: ...


class _DelegatedAgent(Agent):
    """An agent that owns the separately spawned provider handed to it."""

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
    ) -> None:
        self._backend = backend
        self._tools = tools
        self._config = cfg

    def run(self, role: str, task: str) -> str:
        # Imported lazily so registering the delegate tool can finish while the
        # approval gate itself is importing the tool registry.
        from .gate import make_gate

        agent_id, definition = agents.get_delegate(role)
        assigned = task.strip()
        if not assigned:
            raise ValueError("Delegated task must not be empty.")

        backend = spawn_backend(self._backend())
        worker = _DelegatedAgent(
            backend.provider,
            system=(
                prompt.system_prompt(
                    self._config,
                    mode="unattended",
                    agent_id=agent_id,
                    delegation=False,
                    include_memory=False,
                )
                + _DELEGATE_PROMPT
            ),
            tools=self._tools,
            max_iterations=self._config.max_steps,
            extra=backend.extra,
            on_tool_call=make_gate(
                None,
                allow_questions=False,
                allow_delegation=False,
            ),
            # Delegates are threads in the interactive process, so they share
            # its runtime log rather than replacing the process-wide handler.
            **agent_runtime_options(self._config, "interactive"),
        )
        try:
            result = run_sync(worker, assigned).text.strip()
        finally:
            close_sync(worker)
        if not result:
            result = "(The role returned no text.)"
        return f"{definition.name} role result:\n{result}"


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

"""The agent: one chat answer at a time, built fresh from the saved settings.

`Operator.chat` is an async generator of small dicts (the stream the browser reads). Every
check that can fail happens before its first event, so a caller that pulls one event first
can turn those failures into proper HTTP errors before any response starts.
"""

import asyncio
import contextlib
import logging
import re
import secrets
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from datetime import UTC, datetime, timedelta
from typing import Any

from logpose import (
    Agent,
    AuthError,
    LogposeError,
    MaxIterationsError,
    ProviderError,
    RunEnd,
    RunTimeoutError,
    SessionBusyError,
    TextDelta,
    ToolCall,
    ToolResult,
)

from vegapunk.ai import store
from vegapunk.ai.gate import make_gate
from vegapunk.ai.prompt import build_system_prompt
from vegapunk.ai.redact import scrub_secret
from vegapunk.ai.tools import MAX_LOG_LINES, TOOL_NAMES, build_tools
from vegapunk.config import Settings
from vegapunk.db import connect
from vegapunk.host.docker import DockerHost

log = logging.getLogger(__name__)

MAX_ITERATIONS = 8
MAX_TOKENS = 4_096
RUN_TIMEOUT = 180
MAX_TOOL_CALLS = 12
SESSION_TTL = timedelta(days=365)
# logpose adds this read-only tool itself: it pages back through an earlier long tool result.
RECALL_TOOL = "recall_tool_output"

CONVERSATION_ID = re.compile(r"^[A-Za-z0-9_-]{32,64}$")
_RANGE_PHRASE = {
    "1h": "last hour",
    "6h": "last 6 hours",
    "24h": "last 24 hours",
    "7d": "last 7 days",
    "30d": "last 30 days",
}


class OperatorOff(Exception):
    """No AI provider is saved."""


class OperatorBusy(Exception):
    """An answer is already being written."""


class UnknownConversation(Exception):
    """No such conversation: malformed, absent, or deleted."""


def _name(args: dict) -> str | None:
    value = args.get("name")
    return value[:60] if isinstance(value, str) and value else None


def _lines(args: dict) -> int:
    value = args.get("lines", 100)
    if isinstance(value, int) and not isinstance(value, bool):
        return max(1, min(MAX_LOG_LINES, value))
    return 100


def step_label(name: Any, args: Any) -> str:
    """What the agent did, in the past tense. Never raises: a tool call is announced before
    its arguments have been validated, so they can be anything."""
    try:
        args = args if isinstance(args, dict) else {}
        target = _name(args)
        match name:
            case "host_overview":
                return "Read the host's CPU, memory and storage"
            case "list_containers":
                return "Read the status of the containers"
            case "container_details" if target:
                return f"Read the details of `{target}`"
            case "container_metrics" if target:
                phrase = _RANGE_PHRASE.get(args.get("range", "1h"), "last hour")
                return f"Read the {phrase} of CPU and memory for `{target}`"
            case "host_metrics":
                return "Read the host's CPU, memory and storage history"
            case "container_logs" if target:
                return f"Read the last {_lines(args)} log lines of `{target}`"
            case "recall_tool_output":
                return "Read earlier tool output"
        return f"Ran {str(name)[:40]}"
    except Exception:
        return "Ran a tool"


class _StoreOnlyProvider:
    """Lets an Agent read and delete stored conversations with no AI provider configured."""

    name = "store-only"

    async def stream(self, req):  # pragma: no cover - never asked to answer
        raise LogposeError("This agent only reads stored conversations.")
        yield


def _fold(messages) -> list[dict]:
    """Stored logpose messages as what a person saw: their questions, and the agent's
    answers with the steps it took folded in."""
    shown: list[dict] = []
    pending: dict[str, dict] = {}
    answer: dict | None = None
    for message in messages:
        if message.role == "user":
            texts = [b.text for b in message.content if b.type == "text"]
            for block in message.content:
                if block.type == "tool_result" and block.tool_use_id in pending:
                    pending[block.tool_use_id]["status"] = "failed" if block.is_error else "done"
            if texts:
                shown.append({"role": "user", "text": "\n".join(texts), "steps": []})
                answer = None
            continue
        if answer is None:
            answer = {"role": "assistant", "text": "", "steps": []}
            shown.append(answer)
        for block in message.content:
            if block.type == "text" and block.text:
                answer["text"] = (
                    f"{answer['text']}\n\n{block.text}" if answer["text"] else block.text
                )
            elif block.type == "tool_use":
                step = {"label": step_label(block.name, block.input), "status": "done"}
                answer["steps"].append(step)
                pending[block.id] = step
    return shown


def _error(code: str, message: str) -> dict:
    return {"type": "error", "code": code, "message": message}


class Operator:
    def __init__(
        self,
        settings: Settings,
        docker: DockerHost,
        crypto_key: bytes,
        started_at: datetime,
        make_agent: Callable[..., Agent] | None = None,
    ) -> None:
        self._settings = settings
        self._docker = docker
        self._crypto_key = crypto_key
        self._started_at = started_at
        self._make_agent = make_agent or (lambda **kwargs: Agent("openai-compat", **kwargs))
        self._lock = asyncio.Lock()

    # ---- database access from async code
    def _with_db(self, fn):
        conn = connect(self._settings.db_path)
        try:
            return fn(conn)
        finally:
            conn.close()

    async def _db(self, fn):
        return await asyncio.to_thread(self._with_db, fn)

    async def _existing(self, conversation_id: str) -> dict:
        if not CONVERSATION_ID.match(conversation_id):
            raise UnknownConversation
        found = await self._db(lambda c: store.get_conversation(c, conversation_id))
        if found is None:
            raise UnknownConversation
        return found

    def _store_agent(self) -> Agent:
        return Agent(
            _StoreOnlyProvider(),
            model="none",
            db_path=str(self._settings.conversations_path),
            tracing=None,
        )

    # ---- the answer
    async def chat(self, message: str, conversation_id: str | None) -> AsyncIterator[dict]:
        loaded = await self._db(lambda c: store.load_ai(c, self._crypto_key))
        if loaded is None:
            raise OperatorOff
        existing = await self._existing(conversation_id) if conversation_id is not None else None
        if self._lock.locked():
            raise OperatorBusy
        async with self._lock:
            config = loaded.config
            cid = existing["id"] if existing else secrets.token_urlsafe(32)
            title = existing["title"] if existing else store.make_title(message)
            if existing is None:
                await self._db(lambda c: store.add_conversation(c, cid, title, datetime.now(UTC)))
            yield {"type": "conversation", "id": cid, "title": title}

            agent: Agent | None = None
            try:
                host = await self._db(self._host_facts)
                agent = self._make_agent(
                    model=config.model,
                    base_url=config.base_url,
                    # "" and not None: an ambient LOGPOSE_API_KEY must never be sent.
                    api_key=config.api_key or "",
                    system=build_system_prompt(host, config.instructions),
                    tools=build_tools(
                        self._settings.db_path,
                        self._docker,
                        interval=self._settings.capture_interval,
                        started_at=self._started_at,
                    ),
                    on_tool_call=make_gate((*TOOL_NAMES, RECALL_TOOL), MAX_TOOL_CALLS),
                    max_iterations=MAX_ITERATIONS,
                    max_tokens=MAX_TOKENS,
                    run_timeout=RUN_TIMEOUT,
                    tracing=None,
                    db_path=str(self._settings.conversations_path),
                    session_ttl=SESSION_TTL,
                )
                labels: dict[str, str] = {}
                step_ids: dict[str, str] = {}  # the provider's id -> ours, unique per answer
                produced = False
                break_before_text = False
                usage = None
                async with agent.session(cid) as handle:
                    async with aclosing(agent.stream(message, session=handle)) as events:
                        async for event in events:
                            if isinstance(event, TextDelta):
                                if break_before_text and event.text.strip():
                                    yield {"type": "text", "text": "\n\n"}
                                    break_before_text = False
                                produced = produced or bool(event.text.strip())
                                yield {"type": "text", "text": event.text}
                            elif isinstance(event, ToolCall):
                                # Servers may reuse (or omit) ids from one turn to the next.
                                sid = f"s{len(step_ids) + 1}"
                                step_ids[event.id] = sid
                                labels[sid] = step_label(event.name, event.input)
                                break_before_text = produced
                                yield self._step(sid, labels[sid], "running")
                            elif isinstance(event, ToolResult):
                                sid = step_ids.get(event.id) or f"s{len(step_ids) + 1}"
                                label = labels.get(sid) or step_label(event.name, {})
                                yield self._step(sid, label, "failed" if event.is_error else "done")
                            elif isinstance(event, RunEnd):
                                usage = event.result.usage
                if not produced:
                    yield _error("empty", "The agent didn't answer. Try again.")
                else:
                    yield {
                        "type": "done",
                        "usage": {
                            "input_tokens": usage.input_tokens if usage else 0,
                            "output_tokens": usage.output_tokens if usage else 0,
                        },
                    }
            except AuthError:
                await self._db(lambda c: store.mark_key_rejected(c, datetime.now(UTC)))
                yield _error("key_rejected", "Your AI provider rejected the API key.")
            except ProviderError as exc:
                detail = scrub_secret(exc.message, config.api_key)
                yield _error("provider_error", f"The AI provider couldn't answer: {detail}")
            except RunTimeoutError:
                yield _error("timeout", "The agent took too long to answer.")
            except MaxIterationsError:
                yield _error(
                    "too_many_steps", "The agent needed too many steps. Try a narrower question."
                )
            except SessionBusyError:
                yield _error("busy", "The agent is already answering.")
            except Exception:
                log.exception("The agent hit a problem")
                yield _error("error", "The agent hit a problem.")
            finally:
                if agent is not None:
                    with contextlib.suppress(Exception):
                        await agent.aclose()
                try:
                    await self._db(lambda c: store.touch_conversation(c, cid, datetime.now(UTC)))
                except Exception:
                    log.exception("Could not record the end of an answer")

    @staticmethod
    def _step(id: str, label: str, status: str) -> dict:
        return {"type": "step", "id": id, "label": label, "status": status}

    @staticmethod
    def _host_facts(conn) -> dict | None:
        row = conn.execute("SELECT hostname, os FROM host WHERE id = 1").fetchone()
        return {"hostname": row["hostname"], "os": row["os"]} if row else None

    # ---- stored conversations
    async def history(self, conversation_id: str) -> list[dict]:
        await self._existing(conversation_id)
        agent = self._store_agent()
        try:
            return _fold(await agent.load_session(conversation_id))
        finally:
            await agent.aclose()

    async def delete(self, conversation_id: str) -> None:
        await self._existing(conversation_id)
        if self._lock.locked():
            raise OperatorBusy
        async with self._lock:
            agent = self._store_agent()
            try:
                await agent.delete_session(conversation_id)
            finally:
                await agent.aclose()
            await self._db(lambda c: store.delete_conversation(c, conversation_id))

"""The agent: one chat answer at a time, built fresh from the saved settings.

`Operator.chat` is an async generator of small dicts (the stream the browser reads). Every
check that can fail happens before its first event, so a caller that pulls one event first
can turn those failures into proper HTTP errors before any response starts.

`Operator.start_answer` runs `chat` in a task of its own, an `Answer`, which outlives the
request that started it: leaving the page or refreshing does not stop it, any number of
readers can follow it, and only `stop` (or `shutdown`) cancels it.
"""

import asyncio
import contextlib
import logging
import re
import secrets
import sqlite3
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
    ToolDef,
    ToolResult,
)

from vegapunk.ai import store
from vegapunk.ai.gate import make_gate
from vegapunk.ai.prompt import build_system_prompt
from vegapunk.ai.redact import scrub_secret
from vegapunk.ai.store import AiConfig
from vegapunk.ai.tools import MAX_LOG_LINES, TOOL_NAMES, build_tools
from vegapunk.config import Settings
from vegapunk.db import connect
from vegapunk.host.docker import DockerHost
from vegapunk.watch.findings import get_finding

log = logging.getLogger(__name__)

MAX_ITERATIONS = 8
MAX_TOKENS = 4_096
RUN_TIMEOUT = 180
MAX_TOOL_CALLS = 12
SESSION_TTL = timedelta(days=365)
# Events an answer keeps for readers who join late. Past it, step events are left out of the
# record (live readers still get them) and text is merged into one event, so no text is lost.
MAX_EVENTS = 5_000
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


def default_make_agent(**kwargs) -> Agent:
    return Agent("openai-compat", **kwargs)


def build_agent(
    make_agent: Callable[..., Agent],
    *,
    config: AiConfig,
    system: str,
    tools: list[ToolDef],
    on_tool_call: Callable[[Any], str | None],
    max_iterations: int,
    max_tokens: int,
    run_timeout: float,
    db_path: Path | str,
    session_ttl: timedelta | None,
) -> Agent:
    """An agent for the saved provider, with tracing off and every limit stated.

    Shared by chat and by checks, so both reach the provider the same way. `session_ttl`
    None leaves it out (a check keeps no session).
    """
    kwargs: dict[str, Any] = {
        "model": config.model,
        "base_url": config.base_url,
        # "" and not None: an ambient LOGPOSE_API_KEY must never be sent.
        "api_key": config.api_key or "",
        "system": system,
        "tools": tools,
        "on_tool_call": on_tool_call,
        "max_iterations": max_iterations,
        "max_tokens": max_tokens,
        "run_timeout": run_timeout,
        "tracing": None,
        "db_path": str(db_path),
    }
    if session_ttl is not None:
        kwargs["session_ttl"] = session_ttl
    return make_agent(**kwargs)


class OperatorOff(Exception):
    """No AI provider is saved."""


class OperatorBusy(Exception):
    """An answer is already being written."""


class UnknownConversation(Exception):
    """No such conversation: malformed, absent, or deleted."""


class UnknownFinding(Exception):
    """No such finding to start a conversation about: absent, or pruned."""


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


_FINAL = ("done", "error")
_PROBLEM = ("error", "The agent hit a problem.")


class Answer:
    """One answer being written, in a task of its own.

    Every event is recorded (adjacent text merged into one event), so a reader who joins
    late gets what was said so far, then follows live. Readers come and go without
    affecting the answer.
    """

    def __init__(self, question: str = "") -> None:
        self.conversation_id: str | None = None
        self.question = question  # not in the stored history until the answer ends
        self._record: list[dict] = []
        self._followers: set[asyncio.Queue[dict | None]] = set()
        self._ended = asyncio.Event()
        self._task: asyncio.Task | None = None
        self._final = False  # its last event (done or error) is out: only cleanup is left
        self._stopping = False

    @property
    def events(self) -> list[dict]:
        return list(self._record)

    @property
    def done(self) -> bool:
        return self._ended.is_set()

    @property
    def finished(self) -> bool:
        """Its last event (done or error) is out, so the run is stored: only cleanup is
        left. Becomes true before `done`."""
        return self._final

    def _publish(self, event: dict) -> None:
        self._final = self._final or event["type"] in _FINAL
        last = self._record[-1] if self._record else None
        if event["type"] == "text" and last is not None and last["type"] == "text":
            # A new dict: a reader holding the old one must not see it change.
            self._record[-1] = {"type": "text", "text": last["text"] + event["text"]}
        elif event["type"] != "step" or len(self._record) < MAX_EVENTS:
            self._record.append(event)
        for queue in self._followers:
            queue.put_nowait(event)

    def _end(self) -> None:
        self._ended.set()
        for queue in self._followers:
            queue.put_nowait(None)

    async def subscribe(self) -> AsyncIterator[dict]:
        """What was said so far, then each new event until the answer ends."""
        queue: asyncio.Queue[dict | None] = asyncio.Queue()
        replay, ended = list(self._record), self.done
        if not ended:
            self._followers.add(queue)
        try:
            for event in replay:
                yield event
            if ended:
                return
            while (event := await queue.get()) is not None:
                yield event
        finally:
            self._followers.discard(queue)

    def cancel(self) -> bool:
        """Stop the answer. Cancels its task once only: a second cancel would cut short the
        cleanup the first one started. False when it has already ended."""
        if self.done:
            return False
        if not (self._final or self._stopping) and self._task is not None:
            self._stopping = True
            self._task.cancel()
        return True

    async def wait(self) -> None:
        await self._ended.wait()


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
        self._make_agent = make_agent or default_make_agent
        self._lock = asyncio.Lock()
        self._active: dict[str, Answer] = {}  # conversation id -> its running answer
        self._running: set[Answer] = set()

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

    # ---- answers that outlive the request
    async def start_answer(
        self, message: str, conversation_id: str | None, finding_id: int | None = None
    ) -> Answer:
        """Start answering in a task of its own; returns once the first event is out.

        Raises what `chat` raises before its first event (OperatorOff, UnknownConversation,
        UnknownFinding, OperatorBusy), so no answer exists then.
        """
        answer = Answer(message)
        started = asyncio.get_running_loop().create_future()
        # Retrieved even if the caller is gone: no "exception was never retrieved".
        started.add_done_callback(lambda f: f.cancelled() or f.exception())
        answer._task = asyncio.create_task(
            self._produce(answer, self.chat(message, conversation_id, finding_id), started)
        )
        # A task cancelled before its first step never runs `_produce`'s own cleanup.
        answer._task.add_done_callback(lambda _: self._ended(answer, started))
        self._running.add(answer)
        # Shielded: a caller cancelled while waiting leaves the answer running.
        return await asyncio.shield(started)

    async def _produce(self, answer: Answer, events, started: asyncio.Future) -> None:
        try:
            try:
                first = await events.__anext__()
            except BaseException as exc:  # a check failed, or shutdown came first
                if not started.done():
                    if isinstance(exc, asyncio.CancelledError):
                        started.cancel()
                    else:
                        started.set_exception(exc)
                return
            answer.conversation_id = first["id"]
            answer._publish(first)
            self._active[first["id"]] = answer
            if not started.done():
                started.set_result(answer)
            async with aclosing(events):
                async for event in events:
                    answer._publish(event)
        except asyncio.CancelledError:
            # Stopped. `chat` has finished its cleanup and freed the agent by now.
            if not answer._final:
                answer._publish(_error("stopped", "Stopped."))
        except Exception:
            log.exception("An answer stopped unexpectedly")
            if not answer._final:
                answer._publish(_error(*_PROBLEM))
        finally:
            self._ended(answer, started)

    def _ended(self, answer: Answer, started: asyncio.Future) -> None:
        """Forget a finished answer. Safe to call twice."""
        if not started.done():
            started.cancel()
        if self._active.get(answer.conversation_id or "") is answer:
            del self._active[answer.conversation_id]
        self._running.discard(answer)
        if not answer.done:
            answer._end()

    def active(self, conversation_id: str) -> Answer | None:
        """The answer being written in this conversation, if one is."""
        return self._active.get(conversation_id)

    async def stop(self, conversation_id: str, wait: float = 5.0) -> bool:
        """Stop the answer being written in this conversation, and wait (up to `wait`
        seconds) for it to end. False when none is."""
        answer = self._active.get(conversation_id)
        if answer is None or not answer.cancel():
            return False
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(answer.wait(), wait)
        return True

    async def shutdown(self) -> None:
        """Stop every answer and wait for each to finish its cleanup."""
        running = [a for a in self._running if a._task is not None]
        for answer in running:
            answer.cancel()  # also one still in its checks
        if running:
            await asyncio.wait([a._task for a in running])

    # ---- the answer
    async def chat(
        self, message: str, conversation_id: str | None, finding_id: int | None = None
    ) -> AsyncIterator[dict]:
        """Answer `message` in a conversation.

        With `conversation_id`, that conversation continues; if it is about a finding, that
        finding is its context and `finding_id` is ignored. Without one, `finding_id` asks
        about that finding: its conversation resumes if it has one, or one is started.
        """
        loaded = await self._db(lambda c: store.load_ai(c, self._crypto_key))
        if loaded is None:
            raise OperatorOff
        existing = await self._existing(conversation_id) if conversation_id is not None else None
        about: dict | None = None
        if existing is None and finding_id is not None:
            existing, about = await self._db(lambda c: self._for_finding(c, finding_id))
        if self._lock.locked():
            raise OperatorBusy
        async with self._lock:
            config = loaded.config
            if existing is None and about is not None:
                title = store.make_title(f"About: {about['title']}")
                existing = await self._db(
                    lambda c: self._open_finding_conversation(c, about["id"], title)
                )
            cid = existing["id"] if existing else secrets.token_urlsafe(32)
            title = existing["title"] if existing else store.make_title(message)
            if existing is None:
                await self._db(lambda c: store.add_conversation(c, cid, title, datetime.now(UTC)))
            else:  # to the top of History now, not only once a long answer ends
                await self._db(lambda c: store.touch_conversation(c, cid, datetime.now(UTC)))
            linked = existing["finding_id"] if existing else None
            yield {"type": "conversation", "id": cid, "title": title, "finding_id": linked}

            agent: Agent | None = None
            try:
                host, finding = await self._db(lambda c: self._context(c, cid))
                agent = build_agent(
                    self._make_agent,
                    config=config,
                    system=build_system_prompt(host, config.instructions, finding),
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
                    db_path=self._settings.conversations_path,
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

    @classmethod
    def _context(cls, conn, cid: str) -> tuple[dict | None, dict | None]:
        """The host's facts and, read fresh for every answer, the conversation's finding (None
        when it has none, or its finding was pruned)."""
        row = conn.execute("SELECT finding_id FROM conversations WHERE id = ?", (cid,)).fetchone()
        fid = row["finding_id"] if row else None
        return cls._host_facts(conn), get_finding(conn, fid) if fid is not None else None

    @staticmethod
    def _for_finding(conn, finding_id: int) -> tuple[dict | None, dict]:
        """(its conversation or None, the finding). Raises UnknownFinding."""
        found = get_finding(conn, finding_id)
        if found is None:
            raise UnknownFinding
        return store.conversation_for_finding(conn, finding_id), found

    @staticmethod
    def _open_finding_conversation(conn, finding_id: int, title: str) -> dict:
        """The finding's conversation, started now unless one appeared meanwhile. Raises
        UnknownFinding if the finding was pruned since it was looked up."""
        try:
            store.add_conversation(
                conn, secrets.token_urlsafe(32), title, datetime.now(UTC), finding_id=finding_id
            )
        except sqlite3.IntegrityError:
            pass  # the finding is gone (foreign key), or it already has one (unique index)
        found = store.conversation_for_finding(conn, finding_id)
        if found is None:
            raise UnknownFinding
        return found

    # ---- stored conversations
    async def conversation(self, conversation_id: str) -> dict:
        """The stored conversation's row. Raises UnknownConversation."""
        return await self._existing(conversation_id)

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

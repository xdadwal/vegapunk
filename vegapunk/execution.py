"""Persist observed turn lifecycle independently of conversation rollback."""

from dataclasses import dataclass
from uuid import uuid4
import json
import asyncio
from collections.abc import Callable

from logpose import ToolObservation, ToolObserver

from . import db
from .transcript import count_user_turns


@dataclass(frozen=True)
class Run:
    run_id: str
    request: str
    status: str


def start_run(session_id: str, request: str) -> str:
    """Record a request before it can cause any agent action."""
    run_id = str(uuid4())
    with db.transaction(immediate=True) as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,)).fetchone():
            raise ValueError("Unknown session identity")
        conn.execute("INSERT INTO execution_runs (run_id,session_id,request,status,started_at) VALUES (?,?,?,?,?)",
                     (run_id, session_id, request, "running", db.utcnow()))
        conn.execute("INSERT INTO execution_events(run_id,sequence,status,created_at) VALUES (?,1,'running',?)", (run_id, db.utcnow()))
    return run_id


def finish_run(run_id: str, status: str, messages: list[dict] | None = None) -> None:
    """Record a completed, interrupted, or failed turn."""
    if status not in {"completed", "interrupted", "failed"}:
        raise ValueError("Invalid run status")
    with db.transaction(immediate=True) as conn:
        row = conn.execute("SELECT status FROM execution_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row or row[0] != "running":
            raise ValueError("Run is missing or already finished")
        conn.execute("UPDATE execution_runs SET status=?,finished_at=? WHERE run_id=?",
                     (status, db.utcnow(), run_id))
        if messages is not None:
            conn.execute("UPDATE sessions SET messages=?,turns=?,updated_at=? WHERE session_id=(SELECT session_id FROM execution_runs WHERE run_id=?)",
                         (json.dumps(messages), count_user_turns(messages), db.utcnow(), run_id))
        conn.execute("INSERT INTO execution_events(run_id,sequence,status,created_at) VALUES (?,2,?,?)", (run_id, status, db.utcnow()))


def list_runs(session_id: str) -> list[Run]:
    """List recorded runs for a session, newest first."""
    rows = db.query("SELECT run_id,request,status FROM execution_runs WHERE session_id=? ORDER BY started_at DESC", (session_id,))
    return [Run(*row) for row in rows]


def recover_runs() -> None:
    """Mark previous unfinished turns abandoned after acquiring the CLI lock."""
    with db.transaction(immediate=True) as conn:
        conn.execute("INSERT INTO execution_events(run_id,sequence,status,created_at) SELECT run_id,2,'abandoned',? FROM execution_runs WHERE status='running'", (db.utcnow(),))
        conn.execute("UPDATE progress_claims SET active=0,status='abandoned' WHERE active=1")
        conn.execute("UPDATE execution_runs SET status='abandoned',finished_at=? WHERE status='running'", (db.utcnow(),))


def _persist_tool(session_id: str, event: ToolObservation) -> None:
    from . import artifacts

    context = event.context
    with db.transaction(immediate=True) as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,)).fetchone():
            raise ValueError("Unknown session for tool observation")
        if event.phase == "intent":
            stamp = db.utcnow()
            conn.execute("INSERT INTO tool_observations VALUES (?,?,?,?,?,?,?,NULL,?,?)",
                         (str(uuid4()), session_id, context.run_id, event.call.id, event.call.name,
                          json.dumps(event.call.input), "outcome_unknown", stamp, stamp))
            return
        row = conn.execute("SELECT observation_id FROM tool_observations WHERE session_id=? AND runtime_run_id=? AND tool_call_id=? AND status='outcome_unknown'",
                           (session_id, context.run_id, event.call.id)).fetchone()
        if row is None or event.result is None:
            raise ValueError("Tool outcome has no matching unresolved intent")
    # Store the artifact before advertising its reference. If a later write fails,
    # the intent remains unknown and the session snapshot remains retrievable.
    saved = artifacts.store_text(session_id, f"{event.call.name} result", event.result.content)
    with db.transaction(immediate=True) as conn:
        if not conn.execute("SELECT 1 FROM tool_observations WHERE observation_id=? AND session_id=? AND status='outcome_unknown'", (row[0], session_id)).fetchone():
            raise ValueError("Tool intent changed or was removed before outcome persistence")
        conn.execute("UPDATE tool_observations SET status=?,artifact_id=?,updated_at=? WHERE observation_id=? AND session_id=?",
                     ("error" if event.result.is_error else "completed", saved["id"], db.utcnow(), row[0], session_id))


def tool_observer(identity: Callable[[], str | None]) -> ToolObserver:
    """Build an async journal callback; state writes stay joined through cancellation."""
    async def persist(event: ToolObservation) -> None:
        sid = identity()
        if sid is None:
            return
        task = asyncio.create_task(asyncio.to_thread(_persist_tool, sid, event))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
            raise
    return persist


def tool_observations(session_id: str) -> list[dict]:
    """Return recent observed tool outcomes; unmatched intents stay explicitly unknown."""
    rows = db.query("SELECT observation_id,tool_name,status,artifact_id,arguments,runtime_run_id,tool_call_id FROM tool_observations WHERE session_id=? ORDER BY created_at DESC LIMIT 50", (session_id,))
    return [dict(zip(("id", "tool", "status", "artifact_id", "arguments", "runtime_run_id", "tool_call_id"), row)) for row in rows]

"""Persist observed turn lifecycle independently of conversation rollback."""

from dataclasses import dataclass
from uuid import uuid4
import json

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

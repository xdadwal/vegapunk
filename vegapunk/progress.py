"""Transactional session plans and immutable progress snapshots."""

import json
from uuid import uuid4

from . import db


def _checkpoint(conn, session_id: str, plan: dict, reason: str) -> None:
    conn.execute("INSERT INTO progress_checkpoints VALUES (?,?,?,?,?,?,?)",
                 (str(uuid4()), session_id, plan["id"], plan["revision"], reason, json.dumps(plan), db.utcnow()))


def create(session_id: str, title: str, steps: list[str]) -> dict:
    """Create an ordered plan; each step depends on the preceding step."""
    if not title.strip() or not steps or len(steps) > 100 or any(not s.strip() for s in steps):
        raise ValueError("Provide a title and between 1 and 100 nonempty steps")
    plan = {"id": str(uuid4()), "title": title.strip(), "revision": 1,
            "steps": [{"title": s.strip(), "status": "pending", "outcome": "", "evidence": []} for s in steps]}
    with db.transaction(immediate=True) as conn:
        if not conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,)).fetchone():
            raise ValueError("Unknown session")
        if conn.execute("SELECT 1 FROM session_plans WHERE session_id=? AND active=1", (session_id,)).fetchone():
            raise ValueError("An active plan already exists")
        conn.execute("INSERT INTO session_plans VALUES (?,?,?,1,?)", (plan["id"], session_id, 1, json.dumps(plan)))
        _checkpoint(conn, session_id, plan, "created")
    return plan


def get(session_id: str) -> dict | None:
    """Return the latest active plan for this session."""
    rows = db.query("SELECT body FROM session_plans WHERE session_id=? AND active=1", (session_id,))
    return json.loads(rows[0][0]) if rows else None


def update(session_id: str, plan_id: str, step: int, status: str, expected_revision: int,
           outcome: str = "", evidence: list[str] | None = None) -> dict:
    """Validate a transition and append a checkpoint in one transaction."""
    transitions = {"pending": {"in_progress", "cancelled"}, "in_progress": {"completed", "blocked", "cancelled"},
                   "blocked": {"in_progress", "cancelled"}, "completed": set(), "cancelled": set()}
    with db.transaction(immediate=True) as conn:
        row = conn.execute("SELECT body FROM session_plans WHERE plan_id=? AND session_id=? AND active=1", (plan_id, session_id)).fetchone()
        if row is None:
            raise ValueError("Unknown active plan")
        plan = json.loads(row[0])
        if plan["revision"] != expected_revision:
            raise ValueError(f"Stale revision; current revision is {plan['revision']}")
        if isinstance(step, bool) or step < 0 or step >= len(plan["steps"]):
            raise ValueError("Unknown step")
        current = plan["steps"][step]
        if status not in transitions[current["status"]]:
            raise ValueError("Invalid status transition")
        if status in {"in_progress", "completed"} and any(s["status"] != "completed" for s in plan["steps"][:step]):
            raise ValueError("Unfinished dependencies")
        if status == "completed" and (not outcome.strip() or not evidence or any(not e.strip() for e in evidence)):
            raise ValueError("Completion requires an outcome and evidence references")
        current.update(status=status, outcome=outcome, evidence=evidence or [])
        plan["revision"] += 1
        finished = all(s["status"] in {"completed", "cancelled"} for s in plan["steps"])
        conn.execute("UPDATE session_plans SET body=?,revision=?,active=? WHERE plan_id=?",
                     (json.dumps(plan), plan["revision"], int(not finished), plan_id))
        _checkpoint(conn, session_id, plan, f"step {step}: {status}")
    return plan


def checkpoints(session_id: str) -> list[dict]:
    """Return up to fifty recent checkpoints, scoped to the session."""
    return [{"id": row[0], "reason": row[1], "plan": json.loads(row[2])} for row in
            db.query("SELECT checkpoint_id,reason,body FROM progress_checkpoints WHERE session_id=? ORDER BY created_at DESC LIMIT 50", (session_id,))]

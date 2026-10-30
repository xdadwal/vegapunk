"""Transactional session plans and immutable progress snapshots."""

import json
from uuid import uuid4

from . import db


def _checkpoint(conn, session_id: str, plan: dict, reason: str) -> None:
    conn.execute("INSERT INTO progress_checkpoints VALUES (?,?,?,?,?,?,?)",
                 (str(uuid4()), session_id, plan["id"], plan["revision"], reason, json.dumps(plan), db.utcnow()))


def _dependencies(dependencies: list[list[int]], count: int) -> None:
    if len(dependencies) != count:
        raise ValueError("Provide dependencies for every step")
    visiting, visited = set(), set()

    def visit(index: int) -> None:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < count:
            raise ValueError("Invalid dependency index")
        if index in visiting:
            raise ValueError("Dependency cycle")
        if index in visited:
            return
        visiting.add(index)
        for dep in dependencies[index]:
            visit(dep)
        visiting.remove(index)
        visited.add(index)

    for index in range(count):
        visit(index)


def create(session_id: str, title: str, steps: list[str], dependencies: list[list[int]] | None = None, operation_id: str = "") -> dict:
    """Create an ordered plan; each step depends on the preceding step."""
    if not title.strip() or not steps or len(steps) > 100 or any(not s.strip() for s in steps):
        raise ValueError("Provide a title and between 1 and 100 nonempty steps")
    deps = dependencies if dependencies is not None else [[] if i == 0 else [i - 1] for i in range(len(steps))]
    _dependencies(deps, len(steps))
    plan = {"id": str(uuid4()), "title": title.strip(), "revision": 1, "operations": {},
            "steps": [{"id": str(uuid4()), "dependencies": deps[i], "title": s.strip(), "status": "pending", "outcome": "", "evidence": []} for i, s in enumerate(steps)]}
    with db.transaction(immediate=True) as conn:
        payload = json.dumps(["create", title, steps, dependencies])
        if operation_id:
            prior = conn.execute("SELECT o.payload,o.result FROM progress_operations o JOIN session_plans p ON p.plan_id=o.plan_id WHERE p.session_id=? AND o.operation_id=?", (session_id, operation_id)).fetchone()
            if prior:
                if prior[0] != payload:
                    raise ValueError("Operation ID reused with different arguments")
                return json.loads(prior[1])
        if not conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (session_id,)).fetchone():
            raise ValueError("Unknown session")
        if conn.execute("SELECT 1 FROM session_plans WHERE session_id=? AND active=1", (session_id,)).fetchone():
            raise ValueError("An active plan already exists")
        conn.execute("INSERT INTO session_plans VALUES (?,?,?,1,?)", (plan["id"], session_id, 1, json.dumps(plan)))
        _checkpoint(conn, session_id, plan, "created")
        if operation_id:
            conn.execute("INSERT INTO progress_operations VALUES (?,?,?,?)", (plan["id"], operation_id, payload, json.dumps(plan)))
    return plan


def get(session_id: str, plan_id: str = "") -> dict | None:
    """Return the latest active plan for this session."""
    rows = (db.query("SELECT body FROM session_plans WHERE session_id=? AND plan_id=?", (session_id, plan_id)) if plan_id else
            db.query("SELECT body FROM session_plans WHERE session_id=? AND active=1", (session_id,)))
    return json.loads(rows[0][0]) if rows else None


def update(session_id: str, plan_id: str, step: int, status: str, expected_revision: int,
           outcome: str = "", evidence: list[str] | None = None, operation_id: str = "") -> dict:
    """Validate a transition and append a checkpoint in one transaction."""
    transitions = {"pending": {"in_progress", "cancelled"}, "in_progress": {"completed", "blocked", "cancelled"},
                   "blocked": {"in_progress", "cancelled"}, "completed": set(), "cancelled": set()}
    with db.transaction(immediate=True) as conn:
        row = conn.execute("SELECT body FROM session_plans WHERE plan_id=? AND session_id=?", (plan_id, session_id)).fetchone()
        if row is None:
            raise ValueError("Unknown active plan")
        plan = json.loads(row[0])
        payload = json.dumps([step, status, expected_revision, outcome, evidence], sort_keys=True)
        if operation_id:
            prior = conn.execute("SELECT payload,result FROM progress_operations WHERE plan_id=? AND operation_id=?", (plan_id, operation_id)).fetchone()
            if prior:
                if prior[0] != payload:
                    raise ValueError("Operation ID reused with different arguments")
                return json.loads(prior[1])
        if plan["revision"] != expected_revision:
            raise ValueError(f"Stale revision; current revision is {plan['revision']}")
        if isinstance(step, bool) or step < 0 or step >= len(plan["steps"]):
            raise ValueError("Unknown step")
        current = plan["steps"][step]
        if status not in transitions[current["status"]]:
            raise ValueError("Invalid status transition")
        deps = current.get("dependencies", list(range(step)))
        if status in {"in_progress", "completed"} and any(plan["steps"][i]["status"] != "completed" for i in deps):
            raise ValueError("Unfinished dependencies")
        if status == "completed" and (not outcome.strip() or not evidence or any(not e.strip() for e in evidence)):
            raise ValueError("Completion requires an outcome and evidence references")
        current.update(status=status, outcome=outcome, evidence=evidence or [])
        plan["revision"] += 1
        finished = all(s["status"] in {"completed", "cancelled"} for s in plan["steps"])
        conn.execute("UPDATE session_plans SET body=?,revision=?,active=? WHERE plan_id=?",
                     (json.dumps(plan), plan["revision"], int(not finished), plan_id))
        _checkpoint(conn, session_id, plan, f"step {step}: {status}")
        if operation_id:
            conn.execute("INSERT INTO progress_operations VALUES (?,?,?,?)", (plan_id, operation_id, payload, json.dumps(plan)))
    return plan


def revise(session_id: str, plan_id: str, expected_revision: int, reason: str,
           reopen: list[int] | None = None, dependencies: list[list[int]] | None = None,
           add_steps: list[str] | None = None, operation_id: str = "") -> dict:
    """Revise pending dependencies, append steps, or explicitly reopen terminal work."""
    if not reason.strip():
        raise ValueError("A revision reason is required")
    with db.transaction(immediate=True) as conn:
        row = conn.execute("SELECT body FROM session_plans WHERE plan_id=? AND session_id=?", (plan_id, session_id)).fetchone()
        if row is None:
            raise ValueError("Unknown plan")
        plan = json.loads(row[0])
        payload = json.dumps(["revise", expected_revision, reason, reopen, dependencies, add_steps])
        if operation_id:
            prior = conn.execute("SELECT payload,result FROM progress_operations WHERE plan_id=? AND operation_id=?", (plan_id, operation_id)).fetchone()
            if prior:
                if prior[0] != payload:
                    raise ValueError("Operation ID reused with different arguments")
                return json.loads(prior[1])
        if plan["revision"] != expected_revision:
            raise ValueError("Stale revision")
        steps = plan["steps"]
        for index in reopen or []:
            if isinstance(index, bool) or not 0 <= index < len(steps):
                raise ValueError("Unknown step")
            descendants = {index}
            while True:
                expanded = descendants | {i for i, s in enumerate(steps) if set(s.get("dependencies", list(range(i)))) & descendants}
                if expanded == descendants:
                    break
                descendants = expanded
            if any(steps[i]["status"] in {"in_progress", "completed", "blocked"} for i in descendants - {index}):
                raise ValueError("Reopen or cancel descendants first")
            if steps[index]["status"] not in {"completed", "cancelled"}:
                raise ValueError("Only terminal steps can be reopened")
            steps[index].update(status="pending", outcome="", evidence=[])
        for title in add_steps or []:
            if not title.strip() or len(steps) >= 100:
                raise ValueError("Invalid additional step")
            steps.append({"id": str(uuid4()), "title": title.strip(), "status": "pending", "dependencies": [len(steps)-1], "outcome": "", "evidence": []})
        if dependencies is not None:
            _dependencies(dependencies, len(steps))
            for i, deps in enumerate(dependencies):
                if deps != steps[i].get("dependencies", list(range(i))) and steps[i]["status"] != "pending":
                    raise ValueError("Only pending dependencies can change")
                steps[i]["dependencies"] = deps
        if conn.execute("SELECT 1 FROM session_plans WHERE session_id=? AND active=1 AND plan_id!=?", (session_id, plan_id)).fetchone():
            raise ValueError("Another plan is active")
        plan["revision"] += 1
        active = int(any(s["status"] not in {"completed", "cancelled"} for s in steps))
        conn.execute("UPDATE session_plans SET body=?,revision=?,active=? WHERE plan_id=?", (json.dumps(plan), plan["revision"], active, plan_id))
        _checkpoint(conn, session_id, plan, reason)
        if operation_id:
            conn.execute("INSERT INTO progress_operations VALUES (?,?,?,?)", (plan_id, operation_id, payload, json.dumps(plan)))
    return plan


def checkpoints(session_id: str) -> list[dict]:
    """Return up to fifty recent checkpoints, scoped to the session."""
    return [{"id": row[0], "reason": row[1], "plan": json.loads(row[2])} for row in
            db.query("SELECT checkpoint_id,reason,body FROM progress_checkpoints WHERE session_id=? ORDER BY created_at DESC LIMIT 50", (session_id,))]


def claim(session_id: str, plan_id: str, step: int, owner: str) -> None:
    """Atomically reserve one started step for one delegated invocation."""
    with db.transaction(immediate=True) as conn:
        row = conn.execute("SELECT body FROM session_plans WHERE session_id=? AND plan_id=? AND active=1", (session_id, plan_id)).fetchone()
        if row is None:
            raise ValueError("Unknown active plan")
        steps = json.loads(row[0])["steps"]
        if not 0 <= step < len(steps) or steps[step]["status"] != "in_progress":
            raise ValueError("Assigned step must be in progress")
        if conn.execute("SELECT 1 FROM progress_claims WHERE plan_id=? AND step=? AND active=1", (plan_id, step)).fetchone():
            raise ValueError("Assigned step already has an active delegate")
        conn.execute("INSERT INTO progress_claims VALUES (?,?,?,1,'running')", (plan_id, step, owner))


def release(plan_id: str, step: int, owner: str, status: str) -> None:
    """Retain the assignment outcome while releasing its active reservation."""
    db.execute("UPDATE progress_claims SET active=0,status=? WHERE plan_id=? AND step=? AND owner=?", (status, plan_id, step, owner))

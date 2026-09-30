"""Bounded factual feedback, separate from model judgments and personal memory."""

from __future__ import annotations

import json

from . import db, moltbook_actions as actions, moltbook_notebook as notebook

_LABEL = ("Moltbook insights: counts are operational evidence, not quality or engagement. "
          "Confidence, self-review and notebook completion are model-assigned; "
          "API acceptance/read-back is not independent public visibility.\n")
_TASK_IDS = ("SELECT id FROM scheduled_tasks WHERE profile='moltbook' "
             "UNION SELECT task_id FROM moltbook_sources UNION SELECT task_id FROM moltbook_notes "
             "UNION SELECT task_id FROM moltbook_drafts UNION SELECT task_id FROM moltbook_actions "
             "UNION SELECT task_id FROM moltbook_run_scopes UNION SELECT task_id FROM scheduled_runs")


def record_run(task_id: str, run_id: str, profile_since: str) -> None:
    """Attach credential/epoch provenance even to runs deferred before the model."""
    from .tools import moltbook

    key, error = moltbook._load_api_key(moltbook.config.moltbook_credentials_file)
    if error or not key:
        return  # no credential scope exists; human history still records this run
    db.execute("INSERT INTO moltbook_run_scopes VALUES (?,?,?,?)",
               (run_id, task_id, notebook._tag(key), profile_since))


def _counts(sql: str, params: tuple, allowed: tuple[str, ...]) -> dict[str, int]:
    result: dict[str, int] = {}
    for name, count in db.query(sql, params):
        bucket = name if name in allowed else "other"
        result[bucket] = result.get(bucket, 0) + count
    return result


def _aggregate(task_id: str, tag: str = "", epoch: str = "") -> dict:
    scoped = bool(tag)
    params = (task_id, tag, epoch) if scoped else (task_id,)
    where = "task_id=?" + (" AND credential_tag=? AND created_at>=?" if scoped else "")
    drafts_where = "task_id=?" + (" AND credential_tag=? AND profile_since=?" if scoped else "")
    run_where = "r.task_id=?"
    if scoped:
        run_where += (" AND EXISTS (SELECT 1 FROM moltbook_run_scopes s WHERE s.run_id=r.id "
                      "AND s.task_id=r.task_id AND s.credential_tag=? AND s.profile_since=?)")
    result = {
        "task_id": task_id,
        "scope": "current credential/profile epoch" if scoped else "all retained history; legacy run profile/credential attribution may be unknown",
        "runs": _counts(f"SELECT status,COUNT(*) FROM scheduled_runs r WHERE {run_where} GROUP BY status",
                        params, ("running", "completed", "success", "partial", "error", "blocked", "interrupted")),
        "drafts": _counts(f"SELECT status,COUNT(*) FROM moltbook_drafts WHERE {drafts_where} GROUP BY status",
                          params, ("draft", "ready", "revise", "discarded", "published")),
        "self_reviewed_drafts": db.query(f"SELECT COUNT(*) FROM moltbook_drafts WHERE {drafts_where} "
                                          "AND review_run_id<>''", params)[0][0],
        "actions": {
            "states": _counts(f"SELECT state,COUNT(*) FROM moltbook_actions WHERE {drafts_where} GROUP BY state",
                              params, ("sending", "pending_verification", "verifying", "unknown", "accepted",
                                       "read_back_confirmed", "verification_expired", "rejected")),
            "kinds": _counts(f"SELECT kind,COUNT(*) FROM moltbook_actions WHERE {drafts_where} GROUP BY kind",
                             params, ("post", "reply")),
            "unresolved": db.query(f"SELECT COUNT(*) FROM moltbook_actions WHERE {drafts_where} "
                                    "AND state IN ('sending','pending_verification','verifying','unknown')", params)[0][0],
        },
        "notebook": {
            "kinds": _counts(f"SELECT kind,COUNT(*) FROM moltbook_notes WHERE {where} GROUP BY kind",
                             params, ("observation", "hypothesis", "question", "follow_up")),
            "statuses": _counts(f"SELECT status,COUNT(*) FROM moltbook_notes WHERE {where} GROUP BY status",
                                params, ("active", "superseded", "completed")),
            "confidence": _counts(f"SELECT confidence,COUNT(*) FROM moltbook_notes WHERE {where} GROUP BY confidence",
                                  params, ("low", "medium", "high")),
            "overdue_active": db.query(f"SELECT COUNT(*) FROM moltbook_notes WHERE {where} AND status='active' "
                                       "AND follow_up_on<>'' AND follow_up_on<?", (*params, db.utcnow()[:10]))[0][0],
        },
        "sources": db.query(f"SELECT COUNT(*) FROM moltbook_sources WHERE {where}", params)[0][0],
    }
    event_where = "r.task_id=?" + (" AND s.credential_tag=? AND s.profile_since=?" if scoped else "")
    join = "JOIN moltbook_run_scopes s ON s.run_id=r.id" if scoped else ""
    result["tool_outcomes"] = _counts(
        "SELECT e.outcome,COUNT(*) FROM scheduled_run_events e JOIN scheduled_runs r ON r.id=e.run_id "
        f"{join} WHERE {event_where} GROUP BY e.outcome", params,
        ("success", "returned", "pending", "blocked", "error"))
    return result


def lookup(scope: actions.Execution) -> str:
    """No raw text, credentials, cross-task data or unscoped legacy run counts."""
    _, tag = notebook.credentials()
    result = _aggregate(scope.task_id, tag, scope.profile_since)
    actions.check_task(db.get_connection(), scope)
    return _LABEL + json.dumps(result, ensure_ascii=False)


def format_insights(prefix: str = "") -> str:
    """Human retained-history inspection; local only and at most twenty tasks."""
    try:
        prefix = prefix.strip().lower()
        if prefix and (len(prefix) > 32 or any(c not in "0123456789abcdef" for c in prefix)):
            return "Moltbook insight task ID not found or ambiguous."
        sql = f"SELECT id FROM ({_TASK_IDS}) WHERE id LIKE ? || '%' ORDER BY id LIMIT 21"
        rows = db.query(sql, (prefix,))
        if prefix and len(rows) != 1:
            return "Moltbook insight task ID not found or ambiguous."
        result = {"tasks": [_aggregate(row[0]) for row in rows[:20]], "omitted_tasks": len(rows) > 20}
        while len(json.dumps(result)) > 16000:
            result["tasks"].pop()
            result["omitted_tasks"] = True
        return _LABEL + json.dumps(result, ensure_ascii=False)
    except db.StoreError as exc:
        return f"Could not read Moltbook insights: {exc}"

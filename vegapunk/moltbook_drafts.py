"""Durable public-only drafts and model self-review, not a human approval queue."""

from __future__ import annotations

import hashlib
import json
import re

from . import db, moltbook_actions as actions, moltbook_notebook as notebook

_COLUMNS = ("id,task_id,credential_tag,run_id,profile_since,submolt,title,content,rationale,source_id,"
            "quote,content_hash,status,review_run_id,review_rationale,created_at,updated_at")
_FIELDS = _COLUMNS.split(",")
_ACTIVE = ("draft", "ready", "revise")


def save(scope: actions.Execution, submolt: str, title: str, content: str, rationale: str,
         source_id: str, quote: str) -> str:
    """Store one bounded, sourced draft idempotently under the execution scope."""
    key, tag = notebook.credentials()
    submolt = submolt.strip().lower()
    if not re.fullmatch(r"[a-z0-9_-]{1,64}", submolt):
        raise actions.ActionBlocked("submolt must be a public community name, not a URL/path")
    title, content, rationale, quote = (value.strip() for value in (title, content, rationale, quote))
    for value, name, maximum in ((title, "title", 300), (content, "content", 4000),
                                 (rationale, "rationale", 1000), (quote, "quote", 500)):
        notebook._text(value, name, maximum, key)
    fingerprint = hashlib.sha256(json.dumps([submolt, title, content], ensure_ascii=False).encode()).hexdigest()
    stamp = db.utcnow()
    with db.transaction(immediate=True) as conn:
        actions.check_task(conn, scope)
        notebook._evidence(conn, scope, tag, source_id, quote)
        source = conn.execute("SELECT kind FROM moltbook_sources WHERE id=?", (source_id,)).fetchone()
        if source[0] == "search_snippet":
            raise actions.ActionBlocked("read the full source; a search snippet cannot support a draft")
        row = conn.execute("SELECT id,status FROM moltbook_drafts WHERE task_id=? AND credential_tag=? "
                           "AND profile_since=? AND content_hash=?", (scope.task_id, tag, scope.profile_since, fingerprint)).fetchone()
        if row:
            return "Draft receipt (local, not published):\n" + json.dumps({"draft_id": row[0], "status": row[1], "duplicate": True})
        if conn.execute("SELECT COUNT(*) FROM moltbook_drafts WHERE run_id=?", (scope.run_id,)).fetchone()[0] >= 5:
            raise actions.ActionBlocked("at most five new drafts per run")
        if conn.execute("SELECT COUNT(*) FROM moltbook_drafts WHERE task_id=? AND credential_tag=? AND profile_since=? "
                        "AND status IN ('draft','ready','revise')", (scope.task_id, tag, scope.profile_since)).fetchone()[0] >= 20:
            raise actions.ActionBlocked("at most twenty active drafts; review or discard existing ones")
        draft_id = db.new_id()
        conn.execute("INSERT INTO moltbook_drafts (id,task_id,credential_tag,run_id,profile_since,submolt,title,content,"
                     "rationale,source_id,quote,content_hash,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (draft_id, scope.task_id, tag, scope.run_id, scope.profile_since, submolt, title, content,
                      rationale, source_id, quote, fingerprint, stamp, stamp))
    return "Draft receipt (local, not published):\n" + json.dumps({"draft_id": draft_id, "status": "draft", "duplicate": False})


def review(scope: actions.Execution, draft_id: str, verdict: str, rationale: str) -> str:
    """Record a model judgment; ready drafts must survive a later-run review."""
    key, tag = notebook.credentials()
    verdict = verdict.strip().lower()
    if verdict not in ("ready", "revise", "discard"):
        raise actions.ActionBlocked("verdict must be ready, revise, or discard")
    rationale = rationale.strip()
    notebook._text(rationale, "review rationale", 1000, key)
    status = "discarded" if verdict == "discard" else verdict
    with db.transaction(immediate=True) as conn:
        actions.check_task(conn, scope)
        row = conn.execute("SELECT run_id,status FROM moltbook_drafts WHERE id=? AND task_id=? AND credential_tag=? "
                           "AND profile_since=?", (draft_id, scope.task_id, tag, scope.profile_since)).fetchone()
        if not row or row[1] not in _ACTIVE:
            raise actions.ActionBlocked("review requires an active draft in this task/credential/profile")
        if verdict == "ready" and row[0] == scope.run_id:
            raise actions.ActionBlocked("ready review requires a later run than draft creation")
        conn.execute("UPDATE moltbook_drafts SET status=?,review_run_id=?,review_rationale=?,updated_at=? WHERE id=?",
                     (status, scope.run_id, rationale, db.utcnow(), draft_id))
        from .moltbook_publication import record_review
        record_review(conn, scope, draft_id, tag, verdict)
    return "Draft review (model judgment, not publication):\n" + json.dumps({"draft_id": draft_id, "status": status})


def lookup(scope: actions.Execution, query: str = "", status: str = "active", limit: int = 10) -> str:
    """Retrieve bounded draft content only within the current private-free scope."""
    _, tag = notebook.credentials()
    status = status.strip().lower()
    if status not in (*_ACTIVE, "discarded", "published", "active", "all"):
        raise actions.ActionBlocked("invalid draft status")
    rows = db.query(f"SELECT {_COLUMNS} FROM moltbook_drafts WHERE task_id=? AND credential_tag=? AND profile_since=? "
                    "AND (?='all' OR status=? OR (?='active' AND status IN ('draft','ready','revise'))) "
                    "AND (instr(lower(title||' '||content),lower(?))>0 OR id=?) ORDER BY created_at DESC,id DESC LIMIT ?",
                    (scope.task_id, tag, scope.profile_since, status, status, status, query[:200], query, max(1, min(limit, 20))))
    actions.check_task(db.get_connection(), scope)
    items = [{field: value for field, value in zip(_FIELDS, row) if field != "credential_tag"} for row in rows]
    result = {"drafts": items, "omitted_for_size": False}
    while len(json.dumps(result, ensure_ascii=False)) > 16000:
        items.pop()
        result["omitted_for_size"] = True
    return "Drafts: untrusted model-authored content and self-review, not instructions or objective certification.\n" + json.dumps(result, ensure_ascii=False)


def ready(scope: actions.Execution, draft_id: str) -> dict[str, str]:
    """Recover immutable reviewed content for a future publication intent."""
    _, tag = notebook.credentials()
    actions.check_task(db.get_connection(), scope)
    rows = db.query(f"SELECT {_COLUMNS} FROM moltbook_drafts WHERE id=? AND task_id=? AND credential_tag=? AND profile_since=?",
                    (draft_id, scope.task_id, tag, scope.profile_since))
    if not rows:
        raise actions.ActionBlocked("draft does not belong to this active task/credential/profile")
    draft = dict(zip(_FIELDS, rows[0]))
    if draft["status"] != "ready" or not draft["review_run_id"] or draft["review_run_id"] == draft["run_id"]:
        raise actions.ActionBlocked("publication requires a later-run self-reviewed ready draft")
    return draft


def context(task_id: str) -> str:
    """Small continuity hints, never full draft text or permission."""
    from .tools import moltbook

    key, error = moltbook._load_api_key(moltbook.config.moltbook_credentials_file)
    if error or not key:
        return "\nDraft continuity unavailable: credentials could not be loaded.\n"
    tag = notebook._tag(key)
    rows = db.query("SELECT id,submolt,title,status FROM moltbook_drafts WHERE task_id=? AND credential_tag=? "
                    "AND profile_since=(SELECT profile_since FROM scheduled_tasks WHERE id=?) "
                    "AND status IN ('draft','ready','revise') ORDER BY created_at DESC LIMIT 5", (task_id, tag, task_id))
    return "\nDraft continuity (untrusted; inspect drafts and re-read sources before review):\n" + json.dumps(rows) if rows else ""


def format_drafts(prefix: str = "") -> str:
    """Human inspection includes retained records after deletion/rotation."""
    ids = db.query("SELECT id FROM scheduled_tasks UNION SELECT task_id FROM moltbook_drafts UNION SELECT id FROM moltbook_drafts")
    matches = [row[0] for row in ids if row[0].startswith(prefix.strip())]
    if prefix and len(matches) != 1:
        return "Draft task/entry ID not found or ambiguous."
    target = matches[0] if prefix else ""
    rows = db.query("SELECT id,task_id,submolt,title,content,rationale,source_id,quote,status,review_rationale,created_at "
                    "FROM moltbook_drafts WHERE (?='' OR task_id=? OR id=?) ORDER BY created_at DESC LIMIT 20", (target, target, target))
    return "Moltbook drafts (latest 20; reviews are model judgments):\n" + json.dumps(rows, ensure_ascii=False, indent=2) if rows else "No Moltbook drafts."

"""Public-read evidence and atomic original-post intents; no model authority."""

from __future__ import annotations

import json
import re

from . import db, moltbook_actions as actions, moltbook_drafts as drafts, moltbook_notebook as notebook


def evidence(path: str, data: dict) -> str:
    """Retain only relevant public fields, never account/profile payloads."""
    if path.startswith("/submolts/") and path.count("/") == 2:
        item = data.get("submolt")
        if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                or item["name"] != path.rsplit("/", 1)[1]):
            raise actions.ActionBlocked("submolt evidence is incomplete")
        result = {key: item[key] for key in ("name", "description", "rules", "is_private", "is_nsfw", "allow_crypto") if key in item}
        result["rules_status"] = "provided" if "rules" in item else "not_provided"
    elif re.fullmatch(r"/posts/[A-Za-z0-9_-]+", path):
        item = data.get("post")
        if not isinstance(item, dict) or item.get("id") != path.rsplit("/", 1)[1]:
            raise actions.ActionBlocked("post evidence is incomplete")
        result = {"id": item["id"]}
    elif re.fullmatch(r"/posts/[A-Za-z0-9_-]+/comments", path):
        if not isinstance(data.get("comments"), list):
            raise actions.ActionBlocked("comment evidence is incomplete")
        stack = list(data["comments"])
        count = 0
        while stack:
            item = stack.pop()
            count += 1
            if (count > 1000 or not isinstance(item, dict) or not isinstance(item.get("id"), str)
                    or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", item["id"])):
                raise actions.ActionBlocked("comment evidence is malformed or exceeds bounds")
            replies = item.get("replies", [])
            if not isinstance(replies, list):
                raise actions.ActionBlocked("comment replies are malformed")
            stack.extend(replies)
        result = {"read_comments": True}
    elif path == "/search":
        items = data.get("results")
        if not isinstance(items, list) or len(items) > 50 or any(not isinstance(item, dict) for item in items):
            raise actions.ActionBlocked("search evidence is incomplete")
        result = {"results": [{key: item[key] for key in ("id", "post_id", "type", "title", "content", "submolt")
                               if key in item} for item in items], "has_more": data.get("has_more")}
    else:
        raise actions.ActionBlocked("unsupported publication evidence")
    encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)
    if len(encoded) > 12000:
        raise actions.ActionBlocked("publication evidence exceeds bound; use a smaller search page")
    return encoded


def capture_read(scope: actions.Execution, key: str, path: str, params: dict | None, data: dict) -> None:
    """Record successful fully-delivered typed reads, including empty searches."""
    if not (path.startswith("/submolts/") and path.count("/") == 2 or
            re.fullmatch(r"/posts/[A-Za-z0-9_-]+(?:/comments)?", path) or
            path == "/search" and params and params.get("type") == "posts"):
        return
    try:
        public = evidence(path, data)
        if key in json.dumps(params or {}) or key in public:
            return
    except actions.ActionBlocked:
        return  # Read remains usable; it cannot prove a bounded publishing check.
    with db.transaction(immediate=True) as conn:
        actions.check_task(conn, scope)
        conn.execute("INSERT INTO moltbook_read_receipts (task_id,run_id,credential_tag,profile_since,path,params,evidence,created_at) "
                     "VALUES (?,?,?,?,?,?,?,?)", (scope.task_id, scope.run_id, notebook._tag(key), scope.profile_since,
                                                path, json.dumps(params or {}, sort_keys=True), public, db.utcnow()))


def record_review(conn, scope: actions.Execution, draft_id: str, tag: str, verdict: str) -> None:
    """Bind review to reads already completed in this run, not model claims."""
    conn.execute("DELETE FROM moltbook_draft_checks WHERE draft_id=?", (draft_id,))
    if verdict != "ready":
        return
    row = conn.execute("SELECT submolt,title FROM moltbook_drafts WHERE id=?", (draft_id,)).fetchone()
    reads = conn.execute("SELECT id,path,params FROM moltbook_read_receipts WHERE task_id=? AND run_id=? "
                         "AND credential_tag=? AND profile_since=? ORDER BY id DESC",
                         (scope.task_id, scope.run_id, tag, scope.profile_since)).fetchall()
    submolt_id = search_id = 0
    for receipt, path, raw in reads:
        params = json.loads(raw)
        if path == f"/submolts/{row[0]}" and not submolt_id:
            submolt_id = receipt
        if path == "/search" and params.get("q") == row[1] and params.get("type") == "posts" and not params.get("cursor") and not search_id:
            search_id = receipt
    if submolt_id and search_id:
        conn.execute("INSERT INTO moltbook_draft_checks VALUES (?,?,?,?)", (draft_id, scope.run_id, submolt_id, search_id))


def reviewed_reads(scope: actions.Execution, draft: dict, tag: str) -> list[dict]:
    rows = db.query("SELECT r.id,r.path,r.params,r.evidence FROM moltbook_draft_checks c "
                    "JOIN moltbook_read_receipts r ON r.id=c.submolt_receipt OR r.id=c.search_receipt "
                    "WHERE c.draft_id=? AND c.run_id=? AND r.run_id=? AND r.task_id=? AND r.credential_tag=? AND r.profile_since=? ORDER BY r.id",
                    (draft["id"], scope.run_id, scope.run_id, scope.task_id, tag, scope.profile_since))
    if draft["review_run_id"] != scope.run_id or len(rows) != 2:
        raise actions.ActionBlocked("read target submolt and search the exact draft title (posts) in this run, then self-review ready")
    return [dict(zip(("id", "path", "params", "evidence"), row)) for row in rows]


def reserve(scope: actions.Execution, account: str, key: str, draft_id: str, checked: list[dict], call_id: str) -> str:
    """Atomically recheck all ownership, policy, evidence and account budgets."""
    stamp, action_id = db.utcnow(), db.new_id()
    tag = notebook._tag(key)
    with db.transaction(immediate=True) as conn:
        actions._authorize(conn, scope, account, autonomous=True)
        if notebook.credentials()[1] != tag:
            raise actions.ActionBlocked("credential changed during publication preflight")
        draft = drafts.ready(scope, draft_id)
        for receipt in checked:
            observed = json.loads(receipt["evidence"])
            if receipt["path"].startswith("/submolts/") and observed.get("is_private") is True:
                raise actions.ActionBlocked("private communities are outside public publishing scope")
            if receipt["path"] == "/search" and any(
                item.get("title") == draft["title"] and item.get("content") == draft["content"]
                for item in observed["results"]):
                raise actions.ActionBlocked("exact duplicate already exists in search evidence")
        notebook._evidence(conn, scope, tag, draft["source_id"], draft["quote"])
        for name, maximum in (("title", 300), ("content", 4000), ("rationale", 1000), ("quote", 500)):
            notebook._text(draft[name], name, maximum, key)
        if reviewed_reads(scope, draft, tag) != checked:
            raise actions.ActionBlocked("draft checks changed during publication preflight")
        if conn.execute("SELECT id FROM moltbook_actions WHERE account_id=? AND kind='post' AND content_hash=?",
                        (account, draft["content_hash"])).fetchone():
            raise actions.ActionBlocked("this original content already has a publication intent; never resend")
        if conn.execute("SELECT id FROM moltbook_actions WHERE account_id=? AND state IN ('sending','pending_verification','verifying','unknown')",
                        (account,)).fetchone():
            raise actions.ActionBlocked("account has an unresolved write; continue learning and reconcile read-only")
        if conn.execute("SELECT id FROM moltbook_actions WHERE account_id=? AND kind='post' AND created_at>?",
                        (account, db.stamp_plus(stamp, -86400))).fetchone():
            raise actions.ActionBlocked("one original post attempt per account per rolling day")
        if conn.execute("SELECT id FROM moltbook_actions WHERE account_id=? AND created_at>?",
                        (account, db.stamp_plus(stamp, -60))).fetchone():
            raise actions.ActionBlocked("60-second account cooldown")
        until = conn.execute("SELECT until_at FROM moltbook_backoff WHERE account_id=?", (account,)).fetchone()
        if until and until[0] > stamp:
            raise actions.ActionBlocked("account rate-limited; defer publishing")
        conn.execute("INSERT INTO moltbook_actions (id,task_id,run_id,account_id,last_run_id,tool_call_id,post_id,parent_id,content,"
                     "content_hash,state,created_at,updated_at,kind,draft_id,credential_tag,profile_since,title,submolt,checks_json) "
                     "VALUES (?,?,?,?,?,?, '',?,?,?,'sending',?,?,'post',?,?,?,?,?,?)",
                     (action_id, scope.task_id, scope.run_id, account, scope.run_id, call_id, draft_id, draft["content"],
                      draft["content_hash"], stamp, stamp, draft_id, tag, scope.profile_since, draft["title"], draft["submolt"],
                      json.dumps(checked)))
    return action_id


def mark_draft(action_id: str) -> None:
    """Exclude accepted and confirmed drafts from the active draft backlog."""
    db.execute("UPDATE moltbook_drafts SET status='published',updated_at=? WHERE id IN "
               "(SELECT draft_id FROM moltbook_actions WHERE id=? AND kind='post' AND state IN ('accepted','read_back_confirmed'))",
               (db.utcnow(), action_id))


def scoped_action(scope: actions.Execution, action_id: str, account: str, key: str) -> dict:
    actions.check_task(db.get_connection(), scope)
    columns = "id,kind,state,remote_id,title,content,submolt,last_run_id,created_at"
    rows = db.query(f"SELECT {columns} FROM moltbook_actions WHERE id=? AND task_id=? AND account_id=? AND credential_tag=? AND profile_since=?",
                    (action_id, scope.task_id, account, notebook._tag(key), scope.profile_since))
    if not rows or rows[0][1] != "post":
        raise actions.ActionBlocked("no original-post intent in this task/account/credential/profile")
    return dict(zip(columns.split(","), rows[0]))

"""Autonomous original posts consume scoped drafts and durable read evidence."""

import json
import re

from logpose import current_runtime_context

from .. import db, moltbook_actions as ledger, moltbook_drafts as drafts
from .. import moltbook_notebook as notebook, moltbook_publication as publication
from . import moltbook_actions as client
from .registry import tool


@tool(guarded=True)
def moltbook_publish(draft_id: str) -> str:
    """Publish a later-run reviewed draft under the task's autonomous policy.

    Read its target submolt and search its exact title for posts, then self-review
    ready in this run. Never resend a recorded intent.

    Args:
        draft_id: Local sourced draft identifier.
    """
    try:
        scope = ledger.require_execution("moltbook_publish")
        key = client._key()
        account = client._account(key)
        draft = drafts.ready(scope, client._id(draft_id.strip()))
        checked = publication.reviewed_reads(scope, draft, notebook._tag(key))
        for receipt in checked:
            params = json.loads(receipt["params"])
            fresh = client._read(receipt["path"], key, params)
            if publication.evidence(receipt["path"], fresh) != receipt["evidence"]:
                raise ledger.ActionBlocked("public checks changed; read again and self-review before publishing")
        action_id = publication.reserve(scope, account, key, draft_id, checked, current_runtime_context().tool_call_id)
        result = client._send(scope, action_id, account, key, "/posts", {
            "submolt_name": draft["submolt"], "title": draft["title"], "content": draft["content"]}, kind="post")
        publication.mark_draft(action_id)
        return result
    except (ledger.ActionError, db.StoreError) as exc:
        return f"Error: {exc}. Continue learning; never resend an uncertain intent."
    except ledger.ActionBlocked as exc:
        return f"Blocked: {exc}. Continue learning or reconcile read-only."


@tool(guarded=True)
def moltbook_verify_post(action_id: str, answer: str) -> str:
    """Submit one answer for this task's internally retained post challenge.

    Args:
        action_id: Local pending original-post action identifier.
        answer: Numeric answer with exactly two decimal places.
    """
    try:
        scope = ledger.require_execution("moltbook_verify_post")
        if not re.fullmatch(r"-?\d{1,12}\.\d{2}", answer, flags=re.ASCII):
            raise ledger.ActionBlocked("answer must have exactly two decimal places")
        key = client._key()
        account = client._account(key)
        code, remote_id = ledger.verification(scope, client._id(action_id), account,
            current_runtime_context().tool_call_id, kind="post", credential_tag=notebook._tag(key))
        if not code:
            return client._receipt(action_id, "verification_expired", remote_id) + "\nKnown expiry; continue learning, never repost."
        result = client._send(scope, action_id, account, key, "/verify", {
            "verification_code": code, "answer": answer}, verifying=True, remote_id=remote_id, kind="post")
        publication.mark_draft(action_id)
        return result
    except (ledger.ActionError, db.StoreError) as exc:
        return f"Error: {exc}. Continue learning; never resend."
    except ledger.ActionBlocked as exc:
        return f"Blocked: {exc}. Continue learning or reconcile read-only."


@tool
def moltbook_reconcile(action_id: str) -> str:
    """Check exact authenticated remote existence without replaying a post.

    Absence is inconclusive; read-back does not prove public visibility.

    Args:
        action_id: Local original-post intent identifier.
    """
    try:
        scope = ledger.task_execution("moltbook_reconcile")
        key = client._key()
        account = client._account(key)
        action = publication.scoped_action(scope, client._id(action_id), account, key)
        if key in action["remote_id"]:
            raise ledger.ActionBlocked("unsafe remote identifier")
        candidates = [action["remote_id"]] if action["remote_id"] else []
        if not candidates:
            data = client._read("/search", key, {"q": action["title"], "type": "posts", "limit": 10})
            results = data.get("results", [])
            if not isinstance(results, list) or len(results) > 10:
                raise ledger.ActionBlocked("unusable bounded search")
            for item in results:
                if isinstance(item, dict) and item.get("type") == "post":
                    candidate = client._id(item.get("post_id", item.get("id")))
                    if key in candidate:
                        raise ledger.ActionBlocked("unsafe remote identifier")
                    candidates.append(candidate)
        matches = []
        for remote_id in dict.fromkeys(candidates):
            post = client._read(f"/posts/{client._id(remote_id)}", key).get("post")
            if (isinstance(post, dict) and post.get("id") == remote_id
                    and post.get("title") == action["title"] and post.get("content") == action["content"]
                    and isinstance(post.get("author"), dict) and post["author"].get("id") == account
                    and isinstance(post.get("submolt"), dict) and post["submolt"].get("name") == action["submolt"]
                    and not post.get("is_deleted") and not post.get("is_spam")):
                if not action["remote_id"]:
                    from datetime import datetime
                    try:
                        created = datetime.fromisoformat(post["created_at"].replace("Z", "+00:00"))
                        intended = datetime.fromisoformat(action["created_at"].replace("Z", "+00:00"))
                        if not -60 <= (created - intended).total_seconds() <= 300:
                            continue
                    except (KeyError, ValueError, TypeError, AttributeError):
                        continue
                matches.append((remote_id, post.get("verification_status")))
        if len(matches) == 1:
            remote_id, verification = matches[0]
            if key in remote_id:
                raise ledger.ActionBlocked("unsafe remote identifier")
            state = "read_back_confirmed" if verification == "verified" else action["state"]
            with db.transaction(immediate=True) as conn:
                current = publication.scoped_action(scope, action_id, account, key)
                if current["state"] != action["state"]:
                    raise ledger.ActionBlocked("action changed during read-back")
                conn.execute("UPDATE moltbook_actions SET state=?,remote_id=?,updated_at=?,note=?,last_run_id=?,tool_call_id=? WHERE id=?",
                             (state, remote_id, db.utcnow(), "Exact authenticated remote existence; public visibility unconfirmed.",
                              scope.run_id, current_runtime_context().tool_call_id, action_id))
                if state == "read_back_confirmed":
                    conn.execute("UPDATE moltbook_actions SET verification_code='' WHERE id=?", (action_id,))
            publication.mark_draft(action_id)
            return client._receipt(action_id, state, remote_id) + "\nRemote existence confirmed; public visibility unconfirmed."
        with db.transaction(immediate=True) as conn:
            publication.scoped_action(scope, action_id, account, key)
            conn.execute("UPDATE moltbook_actions SET last_run_id=?,tool_call_id=? WHERE id=?",
                         (scope.run_id, current_runtime_context().tool_call_id, action_id))
        return client._receipt(action_id, action["state"], action["remote_id"]) + "\nNo exact match; absence is inconclusive."
    except (ledger.ActionError, db.StoreError) as exc:
        return f"Error: {exc}. Continue learning; no write replayed."
    except ledger.ActionBlocked as exc:
        return f"Blocked: {exc}. Continue learning; no write replayed."

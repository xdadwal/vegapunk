"""Stable message identities stored beside canonical transcripts, never inside them."""

import hashlib
import json
import sys
from uuid import uuid4

from . import db


def digest(message: dict) -> str:
    """Hash canonical JSON without depending on dictionary serialization order."""
    # ASCII JSON also represents escaped lone surrogates in legacy transcripts;
    # indexing must not turn previously inspectable data into a startup failure.
    return hashlib.sha256(json.dumps(message, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode("ascii")).hexdigest()


def sync(conn: db.turso.Connection, session_id: str, messages: list[dict]) -> None:
    """Preserve IDs only through the longest unchanged prefix, within caller transaction."""
    hashes = [digest(message) for message in messages]
    rows = conn.execute("SELECT ordinal,content_hash FROM session_message_ids WHERE session_id=? ORDER BY ordinal", (session_id,)).fetchall()
    prefix = 0
    while prefix < min(len(rows), len(hashes)) and rows[prefix] == (prefix, hashes[prefix]):
        prefix += 1
    conn.execute("DELETE FROM session_message_ids WHERE session_id=? AND ordinal>=?", (session_id, prefix))
    for ordinal in range(prefix, len(messages)):
        conn.execute("INSERT INTO session_message_ids VALUES (?,?,?,?)", (session_id, ordinal, str(uuid4()), hashes[ordinal]))


def references(session_id: str) -> list[dict]:
    """Return scoped metadata; ordinals are locations, not persistent identity."""
    return [dict(zip(("ordinal", "message_id", "content_hash"), row)) for row in
            db.query("SELECT ordinal,message_id,content_hash FROM session_message_ids WHERE session_id=? ORDER BY ordinal", (session_id,))]


def read(session_id: str, message_id: str, content_hash: str) -> dict:
    """Resolve a hash-bound reference, rejecting edits, corruption and foreign sessions."""
    rows = db.query("SELECT r.ordinal,r.content_hash,s.messages FROM session_message_ids r JOIN sessions s ON s.session_id=r.session_id WHERE r.session_id=? AND r.message_id=?", (session_id, message_id))
    if not rows:
        raise ValueError("Unknown message reference in this session")
    ordinal, stored_hash, encoded = rows[0]
    if stored_hash != content_hash:
        raise ValueError("Message reference hash mismatch")
    try:
        messages = json.loads(encoded)
        if not isinstance(messages, list) or not 0 <= ordinal < len(messages) or not isinstance(messages[ordinal], dict) or digest(messages[ordinal]) != content_hash:
            raise ValueError("Referenced transcript changed")
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError("Referenced transcript changed or is unavailable") from exc
    return messages[ordinal]


def backfill(conn: db.turso.Connection) -> None:
    """Assign IDs to existing parseable transcripts without changing their contents."""
    malformed, offset = 0, 0
    while rows := conn.execute("SELECT session_id,messages FROM sessions ORDER BY slug LIMIT 20 OFFSET ?", (offset,)).fetchall():
        for session_id, encoded in rows:
            try:
                messages = json.loads(encoded)
            except (json.JSONDecodeError, TypeError):
                malformed += 1
                continue
            if not session_id or not isinstance(messages, list) or any(not isinstance(message, dict) for message in messages):
                malformed += 1
                continue
            sync(conn, session_id, messages)
        offset += len(rows)
    if malformed:
        print(f"[session references] {malformed} malformed transcript(s) could not be indexed; original data preserved.", file=sys.stderr)

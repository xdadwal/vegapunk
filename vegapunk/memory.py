"""Vegapunk's long-term memory — durable facts that outlive a single session.

A conversation's history lives only as long as the process; this is the part that
persists. Facts are rows in the embedded database (``kind = 'fact'``); the
``remember`` tool inserts them, ``cli.main`` folds them into the system prompt at
session start (``as_system_block``) so the model always *sees* what it knows, and
``/memory list`` / ``/memory forget`` let a human prune them (there is no
hand-editable file any more — use those commands or a sqlite3 client).

The ``kind`` column is deliberately open: only ``'fact'`` is produced today, but
future kinds (episodes, summaries, preferences…) slot in without a schema change.

Note: remembered text is re-injected into the system prompt next session, so it is
model-visible instruction context, not inert data — keep that in mind for what's
worth saving.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

from . import db

_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True)
class Memory:
    """One remembered row. ``created_at`` is the full ISO-8601 UTC stamp; callers
    that want a date show ``created_at[:10]``."""

    id: str
    kind: str
    content: str
    created_at: str


def load_memory() -> str:
    """Render ``fact`` rows as the dated-bullet text folded into the system prompt.

    ``- [YYYY-MM-DD] fact\\n`` per row, oldest first, or ``""`` when empty.
    Best-effort: this runs inline while seeding the system prompt at startup, so a
    database error degrades to "no memory loaded" with a stderr note rather than
    crashing the session.
    """
    try:
        rows = db.query(
            "SELECT content, created_at FROM memory WHERE kind = 'fact' ORDER BY created_at, id"
        )
    except db.StoreError as exc:
        print(f"  [memory] could not read the database: {exc}", file=sys.stderr)
        return ""
    return "".join(f"- [{created_at[:10]}] {content}\n" for content, created_at in rows)


def save_memory(fact: str) -> str:
    """Store ``fact`` as a new ``kind='fact'`` row. Returns a confirmation, or a
    no-op notice for an empty fact. This is the ``remember`` tool's result string.

    (Embeddings for semantic recall are attached here in a later build step; until
    then the ``embedding`` column stays NULL and recall matches on text.)
    """
    fact = fact.strip()
    if not fact:
        return "Nothing to remember — the fact was empty."
    now = db.utcnow()
    try:
        db.execute(
            "INSERT INTO memory (id, kind, content, created_at, updated_at, embedding) "
            "VALUES (?, 'fact', ?, ?, ?, NULL)",
            (db.new_id(), fact, now, now),
        )
    except db.StoreError as exc:
        return f"Could not save to memory: {exc}"
    return f"Saved to memory: {fact}"


def list_memory(kind: str = "fact") -> list[Memory]:
    """Every remembered row of ``kind``, oldest first. ``[]`` (with a stderr note)
    on a database error."""
    try:
        rows = db.query(
            "SELECT id, kind, content, created_at FROM memory WHERE kind = ? ORDER BY created_at, id",
            (kind,),
        )
    except db.StoreError as exc:
        print(f"  [memory] could not list: {exc}", file=sys.stderr)
        return []
    return [Memory(id=r[0], kind=r[1], content=r[2], created_at=r[3]) for r in rows]


def forget_memory(id_prefix: str) -> str:
    """Delete the one fact whose id starts with ``id_prefix`` (git-style short id).

    Returns a human-readable outcome: usage note for a non-hex/empty prefix, a
    not-found note, an ambiguity note when more than one matches, or a
    confirmation naming the deleted fact.
    """
    prefix = id_prefix.strip().lower()
    if not prefix or any(c not in _HEX for c in prefix):
        return "Usage: /memory forget <id>  (id is the short hex shown by /memory list)"
    try:
        rows = db.query("SELECT id, content FROM memory WHERE id LIKE ? || '%'", (prefix,))
    except db.StoreError as exc:
        return f"Could not forget: {exc}"
    if not rows:
        return f"No memory fact matches '{prefix}'."
    if len(rows) > 1:
        return f"'{prefix}' is ambiguous — matches {len(rows)} facts; use more characters."
    fact_id, content = rows[0]
    try:
        db.execute("DELETE FROM memory WHERE id = ?", (fact_id,))
    except db.StoreError as exc:
        return f"Could not forget: {exc}"
    return f"Forgot: {content}"


def recall_memory(query: str, limit: int = 5) -> list[Memory]:
    """Search remembered facts for ones related to ``query``.

    Text match (case-insensitive substring), newest first. A later build step adds
    a semantic (embedding) path in front of this; the text match stays as the
    fallback for when embeddings are disabled or unavailable. ``[]`` (with a
    stderr note) on a database error.
    """
    q = query.strip()
    if not q:
        return []
    try:
        rows = db.query(
            "SELECT id, kind, content, created_at FROM memory "
            "WHERE content LIKE '%' || ? || '%' ORDER BY created_at DESC, id DESC LIMIT ?",
            (q, limit),
        )
    except db.StoreError as exc:
        print(f"  [memory] could not recall: {exc}", file=sys.stderr)
        return []
    return [Memory(id=r[0], kind=r[1], content=r[2], created_at=r[3]) for r in rows]


def as_system_block() -> str:
    """Render saved memory as a system-prompt section, or ``""`` when empty.

    Appended to the system prompt at session start so the model sees its memories
    inline — no separate recall step needed for what it already knows.
    """
    memory = load_memory().strip()
    if not memory:
        return ""
    return (
        "\n\nWhat you remember about the user from past sessions "
        "(call remember to add to this):\n" + memory
    )

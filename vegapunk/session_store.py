"""Save, list, and resume named conversations in the embedded database.

One row per session (``sessions.slug`` is the natural key), holding the message
list as a JSON blob plus a turn count and timestamps. Names are slugified before
they are ever used as a key, so a model- or user-supplied title stays ``[a-z0-9-]``.
All storage failures surface as ``db.StoreError`` (an ``OSError``), so callers can
degrade rather than crash — matching the old flat-file store's posture.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from typing import Literal

from . import db, profiles
from .transcript import count_user_turns

SessionMode = Literal["conversation", "journal"]


def validate_session_mode(value: str) -> SessionMode:
    """Validate persisted session metadata before changing a live conversation."""
    if value == "conversation":
        return "conversation"
    if value == "journal":
        return "journal"
    raise ValueError("Session mode must be conversation or journal")


class SessionNotFound(Exception):
    """No saved session by that name."""


def _is_legacy(messages: list) -> bool:
    """Whether this blob predates the move to logpose's message model.

    The old shape was OpenAI-flavored: a string ``content``, and ``system`` and
    ``tool`` roles as top-level messages. None of that parses now, and there is
    no faithful conversion — the system prompt has moved out of the history
    entirely — so those rows are read as what they are rather than half-restored.
    """
    return any(
        not isinstance(m, dict)
        or isinstance(m.get("content"), str)
        or m.get("role") in ("system", "tool")
        for m in messages
    )


_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 40) -> str:
    """Reduce free text to a safe key of ``[a-z0-9-]`` only.

    Returns ``""`` when nothing usable remains. No slashes, dots, or ``..``
    survive, so a slug is always a safe, self-contained identifier.
    """
    slug = _NON_SLUG.sub("-", text.strip().lower()).strip("-")
    return slug[:max_len].strip("-")


def exists(name: str) -> bool:
    return bool(db.query("SELECT 1 FROM sessions WHERE slug = ?", (name,)))


def unique_name(stem: str) -> str:
    """``stem`` if free, else ``stem-2``, ``stem-3``… so a new session never
    clobbers an existing one. Called once at creation; auto-save then reuses the
    returned name."""
    if not exists(stem):
        return stem
    n = 2
    while exists(f"{stem}-{n}"):
        n += 1
    return f"{stem}-{n}"


def choose_name(suggested: str, fallback_text: str = "") -> str:
    """Pick a free slug for a conversation that doesn't have one yet.

    Three sources in descending order of how much they say about the
    conversation: a model-written title, the text of the first message, then the
    clock. Each is only used if the one before it slugifies to nothing, so an
    empty title (the model declined, or the call failed) degrades to the message
    rather than jumping straight to a timestamp. The result is passed through
    ``unique_name``, so it never clobbers an existing session.
    """
    base = (
        slugify(suggested)
        or slugify(fallback_text)
        or f"session-{datetime.now():%Y%m%d-%H%M%S}"
    )
    return unique_name(base)


def save_session(name: str, messages: list[dict], *, conversation_mode: SessionMode = "conversation",
                 profile: str = "default") -> None:
    """Persist ``messages`` under ``name`` (insert or overwrite). Raises
    ``db.StoreError`` on failure; ``created_at`` is preserved across overwrites."""
    turns = count_user_turns(messages)
    conversation_mode = validate_session_mode(conversation_mode)
    profiles.get_profile(profile)
    now = db.utcnow()
    db.execute(
        "INSERT INTO sessions (slug, messages, turns, created_at, updated_at, conversation_mode, profile) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(slug) DO UPDATE SET "
        "messages = excluded.messages, turns = excluded.turns, updated_at = excluded.updated_at, "
        "conversation_mode = excluded.conversation_mode, profile = excluded.profile",
        (name, json.dumps(messages), turns, now, now, conversation_mode, profile),
    )


def load_session_profile(name: str) -> str:
    """Read and validate the saved voice before replacing the current conversation."""
    rows = db.query("SELECT profile FROM sessions WHERE slug = ?", (name,))
    if not rows:
        raise SessionNotFound(name)
    try:
        profiles.get_profile(rows[0][0])
    except ValueError as exc:
        raise db.StoreError(f"session '{name}' has an unsupported profile") from exc
    return rows[0][0]


def load_session_mode(name: str) -> SessionMode:
    """Read a saved session's mode, rejecting corrupt or unsupported metadata."""
    rows = db.query("SELECT conversation_mode FROM sessions WHERE slug = ?", (name,))
    if not rows:
        raise SessionNotFound(name)
    try:
        return validate_session_mode(rows[0][0])
    except ValueError as exc:
        raise db.StoreError(f"session '{name}' has an unsupported conversation mode") from exc


def load_session(name: str) -> list[dict]:
    """Return the messages saved under ``name``, or raise ``SessionNotFound``.

    A blob that won't parse is database corruption, not a missing session, so it
    raises ``db.StoreError`` rather than masquerading as ``SessionNotFound``.
    """
    rows = db.query("SELECT messages FROM sessions WHERE slug = ?", (name,))
    if not rows:
        raise SessionNotFound(name)
    try:
        messages = json.loads(rows[0][0])
    except (json.JSONDecodeError, TypeError) as exc:
        raise db.StoreError(f"session '{name}' is corrupt: {exc}") from exc
    if isinstance(messages, list) and _is_legacy(messages):
        raise db.StoreError(
            f"session '{name}' was saved by an older Vegapunk and can't be resumed"
        )
    return messages


def delete_session(name: str) -> None:
    """Remove a session, its extraction work, and memories owned by that source."""
    with db.transaction() as conn:
        conn.execute("DELETE FROM sessions WHERE slug = ?", (name,))
        conn.execute("DELETE FROM memory WHERE id IN (SELECT memory_id FROM memory_candidates "
                     "WHERE session_slug=?)", (name,))
        conn.execute("DELETE FROM memory_candidates WHERE session_slug=?", (name,))
        conn.execute("DELETE FROM memory_jobs WHERE session_slug=?", (name,))


def rename_session(old: str, new: str, messages: list[dict], *, conversation_mode: SessionMode | None = None,
                   profile: str | None = None) -> None:
    """Rename atomically, preserving extraction progress and source attribution."""
    if old == new:
        mode = conversation_mode or (load_session_mode(old) if exists(old) else "conversation")
        selected = profile if profile is not None else (load_session_profile(old) if exists(old) else "default")
        save_session(new, messages, conversation_mode=mode, profile=selected)
        return
    encoded, stamp = json.dumps(messages), db.utcnow()
    with db.transaction(immediate=True) as conn:
        if conn.execute("SELECT 1 FROM sessions WHERE slug=?", (new,)).fetchone():
            raise db.StoreError(f"A session named '{new}' already exists")
        source = conn.execute("SELECT created_at,updated_at,messages,conversation_mode,profile FROM sessions WHERE slug=?",
                              (old,)).fetchone()
        mode = validate_session_mode(conversation_mode or (source[3] if source else "conversation"))
        selected = profile if profile is not None else (source[4] if source else "default")
        profiles.get_profile(selected)
        created = source[0] if source else stamp
        updated = source[1] if source and source[2] == encoded else stamp
        conn.execute("INSERT INTO sessions(slug,messages,turns,created_at,updated_at,conversation_mode,profile) "
                     "VALUES (?,?,?,?,?,?,?)", (new, encoded, count_user_turns(messages), created, updated, mode, selected))
        conn.execute("DELETE FROM sessions WHERE slug=?", (old,))
        conn.execute("UPDATE memory_candidates SET session_slug=? WHERE session_slug=?", (new, old))
        # An in-flight result uses the old slug and must not commit after rename.
        conn.execute("UPDATE memory_jobs SET session_slug=?,lease_token=NULL,lease_until=NULL,"
                     "status=CASE WHEN status='running' THEN 'pending' ELSE status END "
                     "WHERE session_slug=?", (new, old))


def list_sessions(limit: int | None = None) -> list[tuple[str, int, str]]:
    """Saved sessions as ``(name, turns, updated_at)``, most recently updated
    first. Pass ``limit`` to cap how many are returned. Degrades to an empty list
    (with a stderr note) if the database can't be read."""
    sql = "SELECT slug, turns, updated_at FROM sessions ORDER BY updated_at DESC, slug"
    try:
        if limit is None:
            rows = db.query(sql)
        else:
            rows = db.query(sql + " LIMIT ?", (limit,))
    except db.StoreError as exc:
        print(f"  [sessions] could not list: {exc}", file=sys.stderr)
        return []
    return [(slug, turns, updated_at) for slug, turns, updated_at in rows]

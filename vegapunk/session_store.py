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

from . import db


class SessionNotFound(Exception):
    """No saved session by that name."""


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


def save_session(name: str, messages: list[dict]) -> None:
    """Persist ``messages`` under ``name`` (insert or overwrite). Raises
    ``db.StoreError`` on failure; ``created_at`` is preserved across overwrites."""
    turns = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "user")
    now = db.utcnow()
    db.execute(
        "INSERT INTO sessions (slug, messages, turns, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(slug) DO UPDATE SET "
        "messages = excluded.messages, turns = excluded.turns, updated_at = excluded.updated_at",
        (name, json.dumps(messages), turns, now, now),
    )


def load_session(name: str) -> list[dict]:
    """Return the messages saved under ``name``, or raise ``SessionNotFound``.

    A blob that won't parse is database corruption, not a missing session, so it
    raises ``db.StoreError`` rather than masquerading as ``SessionNotFound``.
    """
    rows = db.query("SELECT messages FROM sessions WHERE slug = ?", (name,))
    if not rows:
        raise SessionNotFound(name)
    try:
        return json.loads(rows[0][0])
    except (json.JSONDecodeError, TypeError) as exc:
        raise db.StoreError(f"session '{name}' is corrupt: {exc}") from exc


def delete_session(name: str) -> None:
    """Remove a saved session if present (used to rename — drop the old row)."""
    db.execute("DELETE FROM sessions WHERE slug = ?", (name,))


def list_sessions() -> list[tuple[str, int]]:
    """Every saved session as ``(name, turns)``, sorted by name. Degrades to an
    empty list (with a stderr note) if the database can't be read."""
    try:
        rows = db.query("SELECT slug, turns FROM sessions ORDER BY slug")
    except db.StoreError as exc:
        print(f"  [sessions] could not list: {exc}", file=sys.stderr)
        return []
    return [(slug, turns) for slug, turns in rows]

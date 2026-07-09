"""One-time import of the legacy flat-file stores into the database.

Runs on first startup (gated by ``meta.flatfile_migrated``) and leaves the
original files untouched — they are the first backup layer if the beta database
is ever lost.

All three stores plus the completion flag are written in a single transaction, so
a database failure rolls everything back and the next run retries cleanly (no
duplicates). Bad *source* data is different: a corrupt file or line is skipped
with a stderr note and the rest imports — matching the store's "corrupt data
never crashes" posture. The legacy locations are read through the ``legacy_*``
seam functions so tests can point them somewhere harmless.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import turso
from prompt_toolkit.history import FileHistory

from . import db
from .config import config
from .session_store import slugify

_BULLET = re.compile(r"^- \[(\d{4}-\d{2}-\d{2})\] (.+)$")


def legacy_sessions_dir() -> Path:
    return config.sessions_dir


def legacy_memory_path() -> Path:
    return config.memory_file


def legacy_history_path() -> Path:
    return config.history_file


def migrate_if_needed() -> None:
    """Import the flat-file stores once, atomically, then record completion.
    No-op on later runs. Best-effort: a database error aborts (rolls back) with a
    note and is retried next run."""
    try:
        done = db.query("SELECT value FROM meta WHERE key = 'flatfile_migrated'")
    except db.StoreError as exc:
        print(f"  [migrate] skipped (database unavailable): {exc}", file=sys.stderr)
        return
    if done:
        return

    try:
        with db.transaction() as conn:
            sessions = _import_sessions(conn)
            facts = _import_memory(conn)
            history = _import_history(conn)
            conn.execute(
                "INSERT INTO meta (key, value) VALUES ('flatfile_migrated', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (db.utcnow(),),
            )
    except (db.StoreError, turso.Error) as exc:
        print(f"  [migrate] failed, will retry next run: {exc}", file=sys.stderr)
        return

    if sessions or facts or history:
        print(
            f"(migrated {sessions} sessions, {facts} memory facts, {history} history "
            f"entries into {db.db_path()} — originals left in place)"
        )


def _stamp_from_mtime(path: Path) -> str:
    dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _import_sessions(conn: turso.Connection) -> int:
    directory = legacy_sessions_dir()
    if not directory.is_dir():
        return 0
    count = 0
    for path in sorted(directory.glob("*.json")):
        try:
            slug = slugify(path.stem)
            if not slug:
                raise ValueError("no usable slug")
            messages = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(messages, list):
                raise ValueError("session json is not a list of messages")
            turns = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "user")
            stamp = _stamp_from_mtime(path)
            payload = json.dumps(messages)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            print(f"  [migrate] skipped session {path.name}: {exc}", file=sys.stderr)
            continue
        # Outside the try: a database error must abort the whole migration, not
        # be swallowed as a per-file skip.
        conn.execute(
            "INSERT INTO sessions (slug, messages, turns, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(slug) DO NOTHING",
            (slug, payload, turns, stamp, stamp),
        )
        count += 1
    return count


def _import_memory(conn: turso.Connection) -> int:
    path = legacy_memory_path()
    if not path.is_file():
        return 0
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"  [migrate] skipped memory file: {exc}", file=sys.stderr)
        return 0
    today = date.today().isoformat()
    count = 0
    index = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = _BULLET.match(stripped)
        if match:
            day, content = match.group(1), match.group(2)
        else:
            # A freeform (hand-edited) line: keep it, dated today so it sorts
            # after the historical bullets.
            day = today
            content = stripped[2:] if stripped.startswith("- ") else stripped
        # The file-order index in the microsecond slot preserves order among
        # lines that share a date (clamped to keep the field 6 digits — a memory
        # file never approaches a million lines).
        created = f"{day}T00:00:00.{min(index, 999999):06d}Z"
        conn.execute(
            "INSERT INTO memory (id, kind, content, created_at, updated_at, embedding) "
            "VALUES (?, 'fact', ?, ?, ?, NULL)",
            (db.new_id(), content, created, created),
        )
        index += 1
        count += 1
    return count


def _import_history(conn: turso.Connection) -> int:
    path = legacy_history_path()
    if not path.is_file():
        return 0
    try:
        # prompt_toolkit owns the on-disk framing; its loader yields newest first.
        entries = list(FileHistory(str(path)).load_history_strings())
    except (OSError, UnicodeDecodeError) as exc:
        print(f"  [migrate] skipped history file: {exc}", file=sys.stderr)
        return 0
    for entry in reversed(entries):  # oldest first, so id ascends chronologically
        conn.execute(
            "INSERT INTO input_history (entry, created_at) VALUES (?, ?)",
            (entry, db.utcnow()),
        )
    return len(entries)

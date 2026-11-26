"""AI provider settings and the conversation list, in SQLite."""

import sqlite3
from dataclasses import dataclass
from datetime import datetime

from vegapunk.api.queries import z
from vegapunk.crypto import CryptoError, decrypt, encrypt, key_hint
from vegapunk.db import transaction

MAX_URL = 2_048
MAX_MODEL = 200
MAX_INSTRUCTIONS = 4_000
MAX_KEY = 500
TITLE_LENGTH = 60


class _Keep:
    def __repr__(self) -> str:
        return "KEEP"


KEEP = _Keep()  # "leave the stored API key as it is"


@dataclass(frozen=True)
class AiConfig:
    base_url: str
    model: str
    instructions: str
    api_key: str | None


@dataclass(frozen=True)
class LoadedAi:
    config: AiConfig
    key_saved: bool
    key_hint: str | None
    key_rejected_at: datetime | None
    key_unreadable: bool


def normalise_base_url(value: str) -> str:
    url = value.strip()
    if len(url) > MAX_URL:
        raise ValueError(f"Base URL can be up to {MAX_URL:,} characters.")
    if any(c.isspace() for c in url):
        raise ValueError("Base URL can't contain spaces.")
    scheme, sep, rest = url.partition("://")
    if scheme not in ("http", "https") or not sep or not rest.strip("/"):
        raise ValueError("Base URL must start with http:// or https:// and name a server.")
    return url.rstrip("/")


def _check_key(api_key: str) -> str:
    key = api_key.strip()
    if not key:
        raise ValueError("The API key can't be empty. Remove it instead.")
    if len(key) > MAX_KEY:
        raise ValueError(f"The API key can be up to {MAX_KEY} characters.")
    if any(c.isspace() or ord(c) < 32 for c in key):
        raise ValueError("The API key can't contain spaces or line breaks.")
    return key


def load_ai(conn: sqlite3.Connection, crypto_key: bytes) -> LoadedAi | None:
    row = conn.execute("SELECT * FROM ai_settings WHERE id = 1").fetchone()
    if row is None:
        return None
    api_key = None
    unreadable = False
    if row["api_key_encrypted"] is not None:
        try:
            api_key = decrypt(crypto_key, row["api_key_encrypted"])
        except CryptoError:
            unreadable = True
    rejected = row["key_rejected_at"]
    return LoadedAi(
        config=AiConfig(row["base_url"], row["model"], row["instructions"], api_key),
        key_saved=row["api_key_encrypted"] is not None,
        key_hint=row["key_hint"],
        key_rejected_at=datetime.fromisoformat(rejected) if rejected else None,
        key_unreadable=unreadable,
    )


def save_ai(
    conn: sqlite3.Connection,
    crypto_key: bytes,
    *,
    base_url: str,
    model: str,
    instructions: str,
    api_key: "str | None | _Keep",
    now: datetime,
) -> None:
    """Save the settings. `api_key`: KEEP leaves the stored key, None removes it, a string
    replaces it. A stored key is only kept for the base URL it was saved with."""
    url = normalise_base_url(base_url)
    model = model.strip()
    if not 1 <= len(model) <= MAX_MODEL:
        raise ValueError(f"Enter a model name of 1 to {MAX_MODEL} characters.")
    if len(instructions) > MAX_INSTRUCTIONS:
        raise ValueError(f"Instructions can be up to {MAX_INSTRUCTIONS:,} characters.")
    checked_key = _check_key(api_key) if isinstance(api_key, str) else api_key

    with transaction(conn):
        existing = conn.execute("SELECT * FROM ai_settings WHERE id = 1").fetchone()
        if isinstance(checked_key, _Keep):
            encrypted = existing["api_key_encrypted"] if existing else None
            hint = existing["key_hint"] if existing else None
            if encrypted is not None and existing["base_url"] != url:
                raise ValueError(
                    "You changed the Base URL, so enter the API key again, or remove the saved key."
                )
        elif checked_key is None:
            encrypted, hint = None, None
        else:
            encrypted, hint = encrypt(crypto_key, checked_key), key_hint(checked_key)
        conn.execute(
            "INSERT INTO ai_settings (id, base_url, model, instructions, api_key_encrypted, "
            "key_hint, key_rejected_at, updated_at) VALUES (1, ?, ?, ?, ?, ?, NULL, ?) "
            "ON CONFLICT (id) DO UPDATE SET base_url = excluded.base_url, model = excluded.model, "
            "instructions = excluded.instructions, api_key_encrypted = excluded.api_key_encrypted, "
            "key_hint = excluded.key_hint, key_rejected_at = NULL, "
            "updated_at = excluded.updated_at",
            (url, model, instructions, encrypted, hint, now.isoformat()),
        )


def clear_ai(conn: sqlite3.Connection) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM ai_settings")


def mark_key_rejected(conn: sqlite3.Connection, now: datetime) -> None:
    with transaction(conn):
        conn.execute("UPDATE ai_settings SET key_rejected_at = ? WHERE id = 1", (now.isoformat(),))


def clear_key_rejected(conn: sqlite3.Connection) -> None:
    with transaction(conn):
        conn.execute("UPDATE ai_settings SET key_rejected_at = NULL WHERE id = 1")


# ---- conversations
def make_title(message: str) -> str:
    title = " ".join(message.split())
    if not title:
        return "New conversation"
    if len(title) > TITLE_LENGTH:
        return title[: TITLE_LENGTH - 1].rstrip() + "…"
    return title


def add_conversation(conn: sqlite3.Connection, id: str, title: str, now: datetime) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO conversations (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (id, title, now.isoformat(), now.isoformat()),
        )


def touch_conversation(conn: sqlite3.Connection, id: str, now: datetime) -> None:
    with transaction(conn):
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now.isoformat(), id))


def _item(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "title": row["title"], "updated_at": z(row["updated_at"])}


def list_conversations(conn: sqlite3.Connection, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        "SELECT id, title, updated_at FROM conversations ORDER BY updated_at DESC, id LIMIT ?",
        (limit,),
    )
    return [_item(r) for r in rows]


def get_conversation(conn: sqlite3.Connection, id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, title, updated_at FROM conversations WHERE id = ?", (id,)
    ).fetchone()
    return _item(row) if row else None


def delete_conversation(conn: sqlite3.Connection, id: str) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM conversations WHERE id = ?", (id,))

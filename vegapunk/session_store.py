"""Save, list, and resume named conversations on disk.

Pure file IO over message lists — one JSON file per session under
``.vegapunk/sessions/``. Plaintext and human-editable (saved turns can include
remembered facts), so the same no-secrets posture as the history/memory files.
Names are slugified before they ever touch the filesystem, so a model- or
user-supplied title can never escape the sessions directory.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import config


class SessionNotFound(Exception):
    """No saved session by that name."""


def sessions_dir() -> Path:
    """The directory saved sessions live in (a function so tests monkeypatch it,
    mirroring ``memory.memory_path``)."""
    return config.sessions_dir


_NON_SLUG = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, max_len: int = 40) -> str:
    """Reduce free text to a safe filename stem of ``[a-z0-9-]`` only.

    Returns ``""`` when nothing usable remains. Traversal-safe: no slashes, dots,
    or ``..`` survive, so a slug can never escape the sessions directory.
    """
    slug = _NON_SLUG.sub("-", text.strip().lower()).strip("-")
    return slug[:max_len].strip("-")


def _path(name: str) -> Path:
    return sessions_dir() / f"{name}.json"


def exists(name: str) -> bool:
    return _path(name).is_file()


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
    """Write ``messages`` to ``<name>.json``, creating the directory on first use."""
    path = _path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(messages, indent=2), encoding="utf-8")


def load_session(name: str) -> list[dict]:
    """Return the messages saved under ``name``, or raise ``SessionNotFound``."""
    path = _path(name)
    if not path.is_file():
        raise SessionNotFound(name)
    return json.loads(path.read_text(encoding="utf-8"))


def delete_session(name: str) -> None:
    """Remove a saved session if present (used to rename — drop the old file)."""
    _path(name).unlink(missing_ok=True)


def list_sessions() -> list[tuple[str, int]]:
    """Every saved session as ``(name, turns)``, sorted by name.

    A turn is one user message. Unreadable or corrupt files are skipped rather
    than crashing the listing.
    """
    directory = sessions_dir()
    if not directory.is_dir():
        return []
    out: list[tuple[str, int]] = []
    for path in sorted(directory.glob("*.json")):
        try:
            messages = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        turns = sum(1 for m in messages if isinstance(m, dict) and m.get("role") == "user")
        out.append((path.stem, turns))
    return out

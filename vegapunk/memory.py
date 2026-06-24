"""Vegapunk's long-term memory — durable facts that outlive a single session.

A conversation's history lives only as long as the process; this is the part that
persists. The store is a plain, human-editable markdown file (one dated bullet per
fact) under ``.vegapunk/`` — the same plaintext, no-secrets posture as the REPL
history file. The ``remember`` tool appends to it; ``cli.main`` folds the saved
facts into the system prompt at session start (``as_system_block``), so the model
always *sees* what it knows without having to ask for it.

Note: remembered text is re-injected into the system prompt next session, so it is
model-visible instruction context, not inert data — keep that in mind for what's
worth saving.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from .config import config


def memory_path() -> Path:
    """The memory file's path. A function (not a module constant) so tests can
    monkeypatch it without mutating the frozen config — mirrors
    ``workspace.workspace_root``."""
    return config.memory_file


def load_memory() -> str:
    """Return the saved memory text, or ``""`` when nothing has been remembered.

    Best-effort: this runs inline while seeding the system prompt at REPL startup,
    so an unreadable or non-UTF-8 file (the file is hand-editable) degrades to "no
    memory loaded" with a stderr note rather than crashing the session.
    """
    path = memory_path()
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"  [memory] could not read {path}: {exc}", file=sys.stderr)
        return ""


def save_memory(fact: str) -> str:
    """Append ``fact`` to the memory file as a dated bullet, creating the file (and
    its parent dir) on first use. Returns a confirmation, or a no-op notice for an
    empty fact."""
    fact = fact.strip()
    if not fact:
        return "Nothing to remember — the fact was empty."
    path = memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"- [{stamp}] {fact}\n")
    return f"Saved to memory: {fact}"


def as_system_block() -> str:
    """Render saved memory as a system-prompt section, or ``""`` when empty.

    Appended to the system prompt at session start so the model sees its memories
    inline — no separate recall step needed.
    """
    memory = load_memory().strip()
    if not memory:
        return ""
    return (
        "\n\nWhat you remember about the user from past sessions "
        "(call remember to add to this):\n" + memory
    )

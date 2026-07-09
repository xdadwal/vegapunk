"""REPL input history backed by the embedded database.

A ``prompt_toolkit`` ``History`` that stores each submitted line as a row instead
of in a flat file. The base class caches within a prompt session, so we only need
to load newest-first and append. Multi-line entries are a single TEXT value — no
line-framing needed. Both operations degrade with a stderr note on a database
error: losing history recall (or one entry) must never break the prompt.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable

from prompt_toolkit.history import History

from . import db


class DbHistory(History):
    def load_history_strings(self) -> Iterable[str]:
        try:
            rows = db.query("SELECT entry FROM input_history ORDER BY id DESC")
        except db.StoreError as exc:
            print(f"  [history] could not load: {exc}", file=sys.stderr)
            return
        for (entry,) in rows:
            yield entry

    def store_string(self, string: str) -> None:
        try:
            db.execute(
                "INSERT INTO input_history (entry, created_at) VALUES (?, ?)",
                (string, db.utcnow()),
            )
        except db.StoreError as exc:
            print(f"  [history] could not save entry: {exc}", file=sys.stderr)

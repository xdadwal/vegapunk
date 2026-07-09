"""Suite-wide fixtures.

Color hygiene: many tests assert exact plain output substrings, which holds
because capsys streams are not TTYs and the default color mode is "auto". Pin
that state for every test so a developer's shell (VEGAPUNK_COLOR=always or
NO_COLOR exported) can't change what the suite sees. Tests that exercise the
coloring itself override the pin locally by re-monkeypatching vegapunk.style.

Home hygiene: cli.main composes the system prompt from BOTH the memory file
and the skills directory, so any test that drives it would otherwise read the
developer's real .vegapunk/ state. Point both seams at empty tmp locations by
default; tests that exercise memory or skills re-monkeypatch the same seams
at their own paths (a later monkeypatch wins).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from vegapunk import db, style


@pytest.fixture(autouse=True)
def _plain_color_env(monkeypatch):
    monkeypatch.setattr("vegapunk.style.config", replace(style.config, color="auto"))
    monkeypatch.delenv("NO_COLOR", raising=False)


@pytest.fixture(autouse=True)
def _isolated_vegapunk_home(tmp_path, monkeypatch):
    monkeypatch.setattr("vegapunk.db.db_path", lambda: tmp_path / "vegapunk.db")
    monkeypatch.setattr("vegapunk.skills.skills_dir", lambda: tmp_path / "skills")
    # No-network seam: embeddings off by default so tests never hit /embeddings.
    # Tests that exercise the embedding path re-patch this (a later patch wins).
    monkeypatch.setattr("vegapunk.embedding.enabled", lambda: False)
    yield
    db.close_connection()

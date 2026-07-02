"""Suite-wide fixtures.

Color hygiene: many tests assert exact plain output substrings, which holds
because capsys streams are not TTYs and the default color mode is "auto". Pin
that state for every test so a developer's shell (VEGAPUNK_COLOR=always or
NO_COLOR exported) can't change what the suite sees. Tests that exercise the
coloring itself override the pin locally by re-monkeypatching vegapunk.style.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from vegapunk import style


@pytest.fixture(autouse=True)
def _plain_color_env(monkeypatch):
    monkeypatch.setattr("vegapunk.style.config", replace(style.config, color="auto"))
    monkeypatch.delenv("NO_COLOR", raising=False)

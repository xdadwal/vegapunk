"""Enforced tool deadlines: handlers register terminators the loop can run."""

from __future__ import annotations

import requests

from logpose.cancellation import ToolDeadlineHandle, _CURRENT_DEADLINE

from vegapunk.tools import fetch as fetch_module
from tests.test_fetch_tool import _FakeResponse
from vegapunk.tools.deadline import register_terminator


def test_register_terminator_is_a_noop_without_a_deadline():
    assert register_terminator(lambda: None) is False


def test_fetch_registers_session_close_as_terminator(monkeypatch):
    # Force the production session path (tests normally patch fetch._get).
    monkeypatch.setattr(fetch_module, "_get", requests.get)
    monkeypatch.setattr(requests.Session, "get", lambda self, *a, **k: _FakeResponse("<p>ok</p>"))
    handle = ToolDeadlineHandle()
    token = _CURRENT_DEADLINE.set(handle)
    try:
        fetch_module.fetch_url("https://example.com")
    finally:
        _CURRENT_DEADLINE.reset(token)
    assert handle._terminators

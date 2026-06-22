"""Tests for the fetch_url tool — deterministic, no real network.

The HTTP call is the ``_get`` module seam; we monkeypatch it to inject fake
responses (mirroring how the shell tool patches ``_run``).
"""

from __future__ import annotations

from dataclasses import replace

import requests

from vegapunk.config import config
from vegapunk.tools import fetch as fetch_mod
from vegapunk.tools.fetch import fetch_url


class _FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def test_extracts_text_and_strips_markup(monkeypatch):
    html = (
        "<html><head><style>p{color:red}</style></head>"
        "<body><script>evil()</script><p>Hello</p><p>World</p></body></html>"
    )
    monkeypatch.setattr(fetch_mod, "_get", lambda *a, **k: _FakeResponse(html))
    out = fetch_url("http://example.com")
    assert "Hello" in out and "World" in out
    assert "evil()" not in out  # <script> stripped
    assert "color:red" not in out  # <style> stripped


def test_sends_browser_user_agent(monkeypatch):
    # The whole point of the 403 fix: we must NOT send the default python-requests UA.
    seen: dict = {}

    def fake_get(url, headers=None, **kwargs):
        seen["headers"] = headers or {}
        return _FakeResponse("<p>ok</p>")

    monkeypatch.setattr(fetch_mod, "_get", fake_get)
    fetch_url("http://example.com")
    assert "Mozilla" in seen["headers"].get("User-Agent", "")


def test_403_returns_clear_message_not_crash(monkeypatch):
    monkeypatch.setattr(fetch_mod, "_get", lambda *a, **k: _FakeResponse("", status_code=403))
    out = fetch_url("http://blocked.example")
    assert "403" in out
    assert "blocked" in out.lower()


def test_truncates_long_pages(monkeypatch):
    monkeypatch.setattr("vegapunk.tools.fetch.config", replace(config, output_char_cap=50))
    monkeypatch.setattr(fetch_mod, "_get", lambda *a, **k: _FakeResponse("<p>" + "word\n" * 500 + "</p>"))
    out = fetch_url("http://example.com")
    assert out.endswith("...[truncated]")


def test_network_error_returns_clear_string(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("no route to host")

    monkeypatch.setattr(fetch_mod, "_get", boom)
    out = fetch_url("http://nope.example")
    assert out.startswith("Could not reach")


def test_non_403_http_error_returns_clear_string(monkeypatch):
    # A 500 (not 401/403) takes the generic HTTP-error branch, not the bot-block one.
    monkeypatch.setattr(fetch_mod, "_get", lambda *a, **k: _FakeResponse("", status_code=500))
    out = fetch_url("http://broken.example")
    assert "HTTP 500" in out
    assert "blocked" not in out.lower()

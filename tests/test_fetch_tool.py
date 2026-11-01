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
        self.encoding = "utf-8"
        self.closed = False

    def iter_content(self, chunk_size):
        raw = self.text.encode()
        for offset in range(0, len(raw), chunk_size):
            yield raw[offset:offset+chunk_size]

    def close(self):
        self.closed = True

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


def test_download_cap_labels_partial_capture_and_closes_response(monkeypatch):
    from vegapunk import output_capture
    response = _FakeResponse("<p>" + "界" * 30000 + "</p>")
    monkeypatch.setattr(fetch_mod, "DOWNLOAD_LIMIT", 65536)
    seen = {}
    def get(*args, **kwargs):
        seen.update(kwargs)
        return response
    monkeypatch.setattr(fetch_mod, "_get", get)
    output_capture.begin()
    try:
        result = fetch_url("http://example.com")
    finally:
        captured = output_capture.finish()
    assert "download limited" in result
    assert seen["stream"] is True
    assert response.closed
    assert captured is not None and not captured.complete


def test_http_error_closes_response(monkeypatch):
    response = _FakeResponse(status_code=403)
    monkeypatch.setattr(fetch_mod, "_get", lambda *a, **k: response)
    assert "403" in fetch_url("http://example.com")
    assert response.closed


def test_midstream_error_is_reported_and_response_closed(monkeypatch):
    response = _FakeResponse()
    def chunks(chunk_size):
        yield b"<p>partial"
        raise requests.ConnectionError("stream interrupted")
    response.iter_content = chunks
    monkeypatch.setattr(fetch_mod, "_get", lambda *a, **k: response)
    assert "Could not reach" in fetch_url("http://example.com")
    assert response.closed


def test_invalid_charset_is_labelled_instead_of_crashing(monkeypatch):
    response = _FakeResponse("<p>hello</p>")
    response.encoding = "unknown-web-charset"
    monkeypatch.setattr(fetch_mod, "_get", lambda *a, **k: response)
    result = fetch_url("http://example.com")
    assert "hello" in result and "encoding was invalid" in result
    assert response.closed


def test_missing_charset_detects_within_bounded_body(monkeypatch):
    raw = b"<p>caf\xe9</p>"
    reference = requests.Response()
    reference._content = raw
    response = _FakeResponse()
    response.encoding = None
    response.iter_content = lambda chunk_size: iter([raw])
    monkeypatch.setattr(fetch_mod, "_get", lambda *a, **k: response)
    assert reference.text.removeprefix("<p>").removesuffix("</p>") in fetch_url("http://example.com")
    assert response.closed


def test_partial_multibyte_encoding_keeps_valid_prefix(monkeypatch):
    response = _FakeResponse()
    response.encoding = "utf-16-le"
    raw = "<p>hello world".encode("utf-16-le") + b"x"
    response.iter_content = lambda chunk_size: iter([raw])
    monkeypatch.setattr(fetch_mod, "_get", lambda *a, **k: response)
    result = fetch_url("http://example.com")
    assert "hello world" in result and "decoding was lossy" in result

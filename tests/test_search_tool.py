"""Tests for the search_web tool — deterministic, no real network.

The HTTP call is the ``_post`` module seam; we monkeypatch it to inject a fake
DuckDuckGo results page (mirroring how fetch_url patches ``_get``).
"""

from __future__ import annotations

from dataclasses import replace

import requests

from vegapunk.config import config
from vegapunk.tools import search as search_mod
from vegapunk.tools.search import _clean_url, search_web

# A trimmed DuckDuckGo HTML results page: two results, links wrapped in the
# `/l/?uddg=` redirect DDG actually uses.
_PAGE = """
<html><body>
  <div class="result results_links web-result">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Falpha&rut=x">Alpha Result</a>
    <a class="result__snippet">First snippet about alpha.</a>
  </div>
  <div class="result results_links web-result">
    <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fbeta&rut=y">Beta Result</a>
    <a class="result__snippet">Second snippet about beta.</a>
  </div>
</body></html>
"""


class _FakeResponse:
    def __init__(self, text: str = "", status_code: int = 200) -> None:
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


def test_parses_titles_urls_and_snippets(monkeypatch):
    monkeypatch.setattr(search_mod, "_post", lambda *a, **k: _FakeResponse(_PAGE))
    out = search_web("alpha beta")
    assert "Alpha Result" in out
    # The DDG redirect is unwrapped to the real URL.
    assert "https://example.com/alpha" in out
    assert "duckduckgo.com/l/" not in out
    assert "First snippet about alpha." in out


def test_respects_max_results(monkeypatch):
    monkeypatch.setattr(search_mod, "_post", lambda *a, **k: _FakeResponse(_PAGE))
    out = search_web("anything", max_results=1)
    assert "Alpha Result" in out
    assert "Beta Result" not in out  # capped at one


def test_sends_query_and_browser_ua(monkeypatch):
    seen: dict = {}

    def fake_post(url, data=None, headers=None, **kwargs):
        seen["data"] = data or {}
        seen["headers"] = headers or {}
        return _FakeResponse(_PAGE)

    monkeypatch.setattr(search_mod, "_post", fake_post)
    search_web("quantum computing")
    assert seen["data"].get("q") == "quantum computing"
    assert "Mozilla" in seen["headers"].get("User-Agent", "")


def test_no_results_returns_clear_string(monkeypatch):
    monkeypatch.setattr(search_mod, "_post", lambda *a, **k: _FakeResponse("<html><body></body></html>"))
    out = search_web("asdfqwerzxcv")
    assert "No web results" in out


def test_truncates_long_output(monkeypatch):
    monkeypatch.setattr("vegapunk.tools.search.config", replace(config, output_char_cap=40))
    monkeypatch.setattr(search_mod, "_post", lambda *a, **k: _FakeResponse(_PAGE))
    out = search_web("alpha beta")
    assert out.endswith("...[truncated]")


def test_network_error_returns_clear_string(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("dns failure")

    monkeypatch.setattr(search_mod, "_post", boom)
    out = search_web("anything")
    assert out.startswith("Web search for")
    assert "failed" in out


def test_max_results_zero_is_clamped(monkeypatch):
    # max_results=0 must not misreport "no results" — it's clamped to >=1.
    monkeypatch.setattr(search_mod, "_post", lambda *a, **k: _FakeResponse(_PAGE))
    out = search_web("alpha beta", max_results=0)
    assert "Alpha Result" in out
    assert "No web results" not in out


def test_clean_url_unwraps_redirect_passes_through_and_skips_empty():
    assert _clean_url("//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fx&rut=z") == (
        "https://example.com/x"
    )
    assert _clean_url("https://direct.example/page") == "https://direct.example/page"
    assert _clean_url("") == ""

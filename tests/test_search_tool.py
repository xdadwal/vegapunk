"""Tests for the search_web tool — deterministic, no real network.

The HTTP call is the ``_post`` module seam; we monkeypatch it to inject a fake
DuckDuckGo results page (mirroring how fetch_url patches ``_get``).
"""

from __future__ import annotations

from dataclasses import replace
import json

import requests
import pytest

from vegapunk.config import config
from vegapunk.tools import search as search_mod
from vegapunk.tools.search import _clean_url, search_web


@pytest.fixture(autouse=True)
def _no_live_search_credentials(monkeypatch):
    monkeypatch.delenv("ZAI_KEY", raising=False)
    monkeypatch.setattr(search_mod, "config", replace(config, search_provider="duckduckgo"))

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
    monkeypatch.setattr(search_mod, "_post", lambda *a, **k: _FakeResponse(
        '<html><body><div class="no-results">No results found</div></body></html>'))
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


@pytest.mark.parametrize("status", [200, 202])
def test_challenge_is_reported_as_blocking_not_no_results(monkeypatch, status):
    monkeypatch.setattr(search_mod, "_post", lambda *a, **k: _FakeResponse(
        '<form id="challenge-form"><div class="anomaly-modal">Select the ducks</div></form>', status))
    out = search_web("Python documentation")
    assert "blocked" in out.lower()
    assert "No web results" not in out


def test_unexpected_html_does_not_claim_zero_results(monkeypatch):
    monkeypatch.setattr(search_mod, "_post", lambda *a, **k: _FakeResponse("<html>maintenance</html>"))
    out = search_web("Python documentation")
    assert "unrecognized" in out.lower()
    assert "No web results" not in out


def test_blank_query_does_not_reach_network(monkeypatch):
    monkeypatch.setattr(search_mod, "_post", lambda *a, **k: pytest.fail("network called"))
    assert "query" in search_web("  ").lower()


class _RPCResponse(_FakeResponse):
    def __init__(self, payload=None, status_code=200, *, sse=False, headers=None):
        text = "" if payload is None else json.dumps(payload)
        if sse:
            text = ": keepalive\n\nevent: message\ndata: " + text + "\n\n"
        super().__init__(text, status_code)
        self.headers = {"Content-Type": "text/event-stream" if sse else "application/json",
                        **(headers or {})}
        self.closed = False

    def iter_lines(self, **kwargs):
        yield from self.text.splitlines()

    def close(self):
        self.closed = True


def _zai_session(monkeypatch, result, *, sse=False, status_code=200):
    calls = []
    responses = [
        _RPCResponse({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2024-11-05"}},
                     sse=sse, headers={"Mcp-Session-Id": "test-session"}),
        _RPCResponse(status_code=202),
        _RPCResponse({"jsonrpc": "2.0", "id": 2, "result": result},
                     status_code=status_code, sse=sse),
    ]

    class Client:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, url, **kwargs):
            calls.append((url, {**kwargs, "headers": dict(kwargs["headers"])}))
            return responses.pop(0)

    monkeypatch.setattr(search_mod, "_session", Client)
    monkeypatch.setattr(search_mod, "config", replace(config, search_provider="auto"))
    monkeypatch.setenv("ZAI_KEY", "test-secret")
    return calls


@pytest.mark.parametrize("sse", [False, True])
def test_auto_zai_search_uses_coding_plan_mcp_and_real_result_shape(monkeypatch, sse):
    items = [
        {"title": "Python docs", "link": "https://docs.python.org/3/", "content": "Reference"},
        {"title": "Python", "link": "https://python.org/", "content": "Home"},
    ]
    result = {"content": [{"type": "text", "text": json.dumps(json.dumps(items))}], "isError": False}
    calls = _zai_session(monkeypatch, result, sse=sse)
    out = search_web("Python documentation", max_results=1)
    assert "Python docs" in out
    assert "https://docs.python.org/3/" in out
    assert "https://python.org/" not in out
    assert "test-secret" not in out
    assert len(calls) == 3
    assert all(url == "https://api.z.ai/api/mcp/web_search_prime/mcp" for url, _ in calls)
    assert all(call["allow_redirects"] is False for _, call in calls)
    assert calls[0][1]["json"]["method"] == "initialize"
    assert calls[1][1]["json"]["method"] == "notifications/initialized"
    assert calls[2][1]["json"]["params"] == {
        "name": "web_search_prime", "arguments": {"search_query": "Python documentation", "location": "us"}}
    assert calls[2][1]["headers"]["Mcp-Session-Id"] == "test-session"


@pytest.mark.parametrize("code", [401, 403, 429, 500, 302])
def test_zai_http_failures_do_not_silently_fallback_or_expose_key(monkeypatch, code):
    calls = _zai_session(monkeypatch, {}, status_code=code)
    monkeypatch.setattr(search_mod, "_post", lambda *a, **k: pytest.fail("unexpected fallback"))
    out = search_web("Python")
    assert "failed" in out.lower()
    assert str(code) in out
    assert "test-secret" not in out
    assert len(calls) == 3


def test_zai_tool_errors_are_not_no_results(monkeypatch):
    _zai_session(monkeypatch, {"isError": True, "content": [{"type": "text", "text": "test-secret"}]})
    out = search_web("Python")
    assert "failed" in out.lower()
    assert "No web results" not in out
    assert "test-secret" not in out


def test_explicit_zai_requires_credential(monkeypatch):
    monkeypatch.setattr(search_mod, "config", replace(config, search_provider="zai"))
    assert "ZAI_KEY" in search_web("Python")


def test_auto_without_key_uses_duckduckgo(monkeypatch):
    monkeypatch.setattr(search_mod, "config", replace(config, search_provider="auto"))
    monkeypatch.setattr(search_mod, "_post", lambda *a, **k: _FakeResponse(_PAGE))
    monkeypatch.setattr(search_mod, "_session", lambda: pytest.fail("Z.ai called without key"))
    assert "Alpha Result" in search_web("alpha")


def test_explicit_duckduckgo_does_not_use_zai_key(monkeypatch):
    monkeypatch.setenv("ZAI_KEY", "test-secret")
    monkeypatch.setattr(search_mod, "_post", lambda *a, **k: _FakeResponse(_PAGE))
    monkeypatch.setattr(search_mod, "_session", lambda: pytest.fail("explicit provider ignored"))
    assert "Alpha Result" in search_web("alpha")


@pytest.mark.parametrize("content", [[], [{"type": "text", "text": "broken"}],
                                      [{"type": "text", "text": '{"error":"test-secret"}'}]])
def test_malformed_zai_results_do_not_claim_empty_search(monkeypatch, content):
    _zai_session(monkeypatch, {"content": content})
    out = search_web("Python")
    assert "failed" in out.lower()
    assert "No web results" not in out
    assert "test-secret" not in out


def test_explicit_empty_zai_results_are_genuinely_empty(monkeypatch):
    _zai_session(monkeypatch, {"content": [{"type": "text", "text": json.dumps(json.dumps([]))}]})
    assert "No web results" in search_web("none")


def test_unrecognized_search_provider_is_reported(monkeypatch):
    monkeypatch.setattr(search_mod, "config", replace(config, search_provider="unknown"))
    assert "VEGAPUNK_SEARCH_PROVIDER" in search_web("Python")


def test_ddg_rate_limit_is_not_empty_results(monkeypatch):
    monkeypatch.setattr(search_mod, "_post", lambda *a, **k: _FakeResponse("Rate limit", 429))
    assert "failed" in search_web("Python")


@pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/plain,test", "/relative", "http://["])
def test_result_urls_are_usable_http_links(url):
    assert _clean_url(url) == ""


def test_rpc_parses_sse_matching_response_after_notification():
    response = _RPCResponse(sse=True)
    response.text = (': keepalive\n\ndata: {"jsonrpc":"2.0","method":"notice"}\n\n'
                     'event: message\ndata: {"jsonrpc":"2.0","id":2,\n'
                     'data: "result":{"content":[]}}\n\n')

    class Client:
        def post(self, *args, **kwargs):
            return response

    assert search_mod._rpc(Client(), {}, "tools/call", {}, 2) == {"content": []}


def test_rpc_error_does_not_echo_server_secret():
    response = _RPCResponse({"jsonrpc": "2.0", "id": 2,
                             "error": {"code": -32000, "message": "test-secret"}})

    class Client:
        def post(self, *args, **kwargs):
            return response

    with pytest.raises(ValueError) as raised:
        search_mod._rpc(Client(), {}, "tools/call", {}, 2)
    assert "test-secret" not in str(raised.value)


def test_sse_returns_matching_result_without_waiting_for_eof():
    class OpenStream(_RPCResponse):
        def iter_lines(self, **kwargs):
            yield 'data: {"jsonrpc":"2.0","id":2,"result":{"content":[]}}'
            yield ''
            pytest.fail("waited for EOF after a complete matching event")

    response = OpenStream(sse=True)

    class Client:
        def post(self, *args, **kwargs):
            assert kwargs["stream"] is True
            return response

    assert search_mod._rpc(Client(), {}, "tools/call", {}, 2) == {"content": []}
    assert response.closed

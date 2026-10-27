"""Web search via Z.ai Coding Plan MCP or keyless DuckDuckGo HTML."""

from __future__ import annotations

import json
import os
from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from ..config import config
from .registry import tool

_SEARCH_URL = "https://html.duckduckgo.com/html/"
_ZAI_URL = "https://api.z.ai/api/mcp/web_search_prime/mcp"

# Same browser-UA story as fetch_url: the default python-requests UA gets blocked.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Module-level seam: tests patch this to inject a results page without real network.
_post = requests.post
_session = requests.Session


class _SearchFailure(ValueError):
    """A provider failure safe to show without headers or raw server payloads."""


@tool
def search_web(query: str, max_results: int = 5) -> str:
    """Search the web for external or up-to-date information and return the top
    results as `title — url` with a snippet. Use this when the answer isn't in
    the workspace or your own knowledge; then call fetch_url on a result's URL to
    read the full page. Reports provider blocking/errors distinctly from an empty
    search. Uses configured search provider independently of the chat model.

    Args:
        query: Search terms or a natural-language question.
        max_results: Maximum number of results, clamped to 1 through 25.
    """
    # Be lenient with model-supplied counts: clamp to a sane range so a stray 0
    # or a huge value doesn't misreport "no results" or over-fetch.
    max_results = max(1, min(max_results, 25))
    query = query.strip()
    if not query:
        return "Web search needs a non-empty query."
    provider = config.search_provider
    key = os.getenv("ZAI_KEY", "").strip()
    if provider == "auto":
        provider = "zai" if key else "duckduckgo"
    if provider not in ("zai", "duckduckgo"):
        return "Web search failed: VEGAPUNK_SEARCH_PROVIDER must be auto, zai, or duckduckgo."
    if provider == "zai":
        if not key:
            return "Web search failed: Z.ai Coding Plan search requires ZAI_KEY."
        try:
            results = _zai_search(query, max_results, key)
        except (requests.RequestException, _SearchFailure) as exc:
            # Request exception text/raw RPC messages may contain credentials or
            # response bodies. Only our controlled failures are model-visible.
            detail = str(exc) if isinstance(exc, _SearchFailure) else type(exc).__name__
            return f"Web search for {query!r} failed (Z.ai Coding Plan): {detail}"
        return _format_results(query, results)

    try:
        response = _post(
            _SEARCH_URL, data={"q": query}, headers=_HEADERS, timeout=15, allow_redirects=True
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return f"Web search for {query!r} failed: {exc}"

    soup = BeautifulSoup(response.text, "html.parser")
    if soup.select_one("#challenge-form, #anomaly-form, .anomaly-modal"):
        return (f"Web search for {query!r} blocked by DuckDuckGo human verification "
                f"(HTTP {response.status_code}). This is not an empty search. "
                "Do not retry the same request; configure Z.ai Coding Plan search with ZAI_KEY, "
                "or fetch a known source URL directly.")
    if response.status_code != 200:
        return f"Web search for {query!r} failed: unexpected DuckDuckGo HTTP {response.status_code}."
    results = _parse_results(response.text, max_results)
    if not results and not soup.select_one(".no-results, .result--no-result"):
        return (f"Web search for {query!r} failed: unrecognized DuckDuckGo response. "
                "No result count could be established.")
    return _format_results(query, results)


def _format_results(query: str, results: list[str]) -> str:
    if not results:
        return f"No web results for {query!r}. Try different or broader search terms."

    body = "\n\n".join(results)
    if len(body) > config.output_char_cap:
        body = body[: config.output_char_cap] + "\n...[truncated]"
    return f"Web results for {query!r}:\n\n{body}"


def _rpc(client: requests.Session, headers: dict[str, str], method: str,
         params: dict | None = None, request_id: int | None = None) -> dict:
    """Make one bounded MCP HTTP exchange; notifications may return empty 202."""
    payload: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        payload["params"] = params
    if request_id is not None:
        payload["id"] = request_id
    response = client.post(_ZAI_URL, json=payload, headers=headers, timeout=30,
                           allow_redirects=False, stream=True)
    try:
        session_id = response.headers.get("Mcp-Session-Id")
        if session_id:
            headers["Mcp-Session-Id"] = session_id
        if request_id is None and response.status_code in (200, 202):
            return {}  # Notifications have no JSON-RPC response body.
        if response.status_code != 200:
            hints = {401: "check ZAI_KEY", 403: "check Coding Plan access",
                     429: "Coding Plan quota or rate limit reached"}
            hint = hints.get(response.status_code, "service request was unsuccessful")
            raise _SearchFailure(f"HTTP {response.status_code}: {hint}.")
        message = _rpc_message(response, request_id)
    finally:
        # A matching result can arrive long before the SSE connection closes.
        response.close()
    if not isinstance(message, dict) or message.get("id") != request_id:
        raise _SearchFailure("Unrecognized MCP response or request identifier.")
    if "error" in message:
        raise _SearchFailure("MCP request rejected; check Coding Plan credentials and quota.")
    result = message.get("result")
    if not isinstance(result, dict):
        raise _SearchFailure("MCP response is missing its result.")
    return result


def _rpc_message(response: requests.Response, request_id: int | None) -> object:
    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
    if content_type != "text/event-stream":
        try:
            return json.loads(response.text)
        except ValueError as exc:
            raise _SearchFailure("Malformed MCP JSON response.") from exc
    response.encoding = "utf-8"  # SSE is UTF-8, including non-ASCII result snippets.
    data: list[str] = []
    received = 0
    # A larger fixed read can wait for more bytes after a short complete event
    # on a persistent connection. Read incrementally and return at its boundary.
    for line in response.iter_lines(chunk_size=1, decode_unicode=True):
        received += len(line.encode("utf-8"))
        if received > 2_000_000:
            raise _SearchFailure("MCP response exceeded the search size limit.")
        if line.startswith("data:"):
            data.append(line[5:].removeprefix(" "))
        elif not line and data:
            try:
                candidate = json.loads("\n".join(data))
            except ValueError as exc:
                raise _SearchFailure("Malformed MCP event response.") from exc
            data.clear()
            if isinstance(candidate, dict) and candidate.get("id") == request_id:
                return candidate
    raise _SearchFailure("MCP stream ended without a matching result.")


def _zai_search(query: str, max_results: int, key: str) -> list[str]:
    headers = {"Authorization": f"Bearer {key}", "Accept": "application/json, text/event-stream"}
    with _session() as client:
        initial = _rpc(client, headers, "initialize", {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "vegapunk", "version": "0.1.0"},
        }, 1)
        version = initial.get("protocolVersion")
        if not isinstance(version, str) or not version:
            raise _SearchFailure("MCP initialization did not negotiate a protocol version.")
        headers["MCP-Protocol-Version"] = version
        _rpc(client, headers, "notifications/initialized")
        result = _rpc(client, headers, "tools/call", {
            "name": "web_search_prime", "arguments": {"search_query": query, "location": "us"},
        }, 2)
    if result.get("isError"):
        raise _SearchFailure("Search tool returned an error; check Coding Plan access and quota.")
    content = result.get("content")
    if not isinstance(content, list):
        raise _SearchFailure("Search tool response has no content blocks.")
    items: list[dict] = []
    decoded = False
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "text":
            continue
        value = block.get("text")
        # The live service wraps its JSON result list in a JSON string.
        for _ in range(3):
            if not isinstance(value, str):
                break
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise _SearchFailure("Unrecognized search result format.") from exc
        if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
            raise _SearchFailure("Unrecognized search result list.")
        decoded = True
        items.extend(value)
    if not decoded:
        raise _SearchFailure("Search tool returned no recognized results payload.")
    out = []
    for item in items:
        title, url = item.get("title"), item.get("link")
        if not isinstance(title, str) or not title.strip() or not isinstance(url, str):
            raise _SearchFailure("Search result is missing a title or URL.")
        cleaned = _clean_url(url)
        if not cleaned:
            raise _SearchFailure("Search result has an invalid URL.")
        entry = f"{title.strip()} — {cleaned}"
        snippet = item.get("content")
        if isinstance(snippet, str) and snippet.strip():
            entry += "\n  " + snippet.strip()
        out.append(entry)
        if len(out) >= max_results:
            break
    return out


def _parse_results(html: str, max_results: int) -> list[str]:
    """Pull `title — url` + snippet out of a DuckDuckGo HTML results page."""
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for result in soup.select("div.result"):
        link = result.select_one("a.result__a")
        if link is None:
            continue  # ads / non-result blocks
        url = _clean_url(link.get("href", ""))
        if not url:
            continue
        title = link.get_text(strip=True)
        snippet_el = result.select_one(".result__snippet")
        entry = f"{title} — {url}"
        if snippet_el:
            snippet = snippet_el.get_text(" ", strip=True)
            if snippet:
                entry += f"\n  {snippet}"
        out.append(entry)
        if len(out) >= max_results:
            break
    return out


def _clean_url(href: str) -> str:
    """DDG wraps result links in a `/l/?uddg=<real-url>` redirect; unwrap it."""
    if not isinstance(href, str) or not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urlparse(href)
        host = parsed.hostname or ""
        if ((host == "duckduckgo.com" or host.endswith(".duckduckgo.com") or not host)
                and parsed.path.startswith("/l/")):
            target = parse_qs(parsed.query).get("uddg", [])
            href = target[0] if target else ""
            parsed = urlparse(href)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return ""
    except ValueError:
        return ""
    return href

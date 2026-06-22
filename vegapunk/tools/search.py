"""A read-only tool that searches the web via DuckDuckGo's HTML endpoint.

No API key and no extra dependency — it reuses requests + bs4 (the same stack as
fetch_url). DuckDuckGo wraps each result link in a redirect; we unwrap it so the
model gets real URLs it can hand straight to fetch_url.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import requests
from bs4 import BeautifulSoup

from ..config import config
from .registry import tool

_SEARCH_URL = "https://html.duckduckgo.com/html/"

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


@tool
def search_web(query: str, max_results: int = 5) -> str:
    """Search the web for external or up-to-date information and return the top
    results as `title — url` with a snippet. Use this when the answer isn't in
    the workspace or your own knowledge; then call fetch_url on a result's URL to
    read the full page. Returns an error string (not a crash) if the search can't
    be reached."""
    # Be lenient with model-supplied counts: clamp to a sane range so a stray 0
    # or a huge value doesn't misreport "no results" or over-fetch.
    max_results = max(1, min(max_results, 25))
    try:
        response = _post(
            _SEARCH_URL, data={"q": query}, headers=_HEADERS, timeout=15, allow_redirects=True
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        return f"Web search for {query!r} failed: {exc}"

    results = _parse_results(response.text, max_results)
    if not results:
        return f"No web results for {query!r}. Try different or broader search terms."

    body = "\n\n".join(results)
    if len(body) > config.output_char_cap:
        body = body[: config.output_char_cap] + "\n...[truncated]"
    return f"Web results for {query!r}:\n\n{body}"


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
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [])
        return target[0] if target else ""
    return href

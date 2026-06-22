"""A read-only tool that fetches the readable text of a web page.

Reaches the network (unlike the workspace-confined file tools), so it is the
first tool that lets Vegapunk see outside the machine. Many sites reject the
default ``python-requests`` User-Agent with a 403, so we send realistic browser
headers; output is capped to protect the context window.
"""

from __future__ import annotations

import requests
from bs4 import BeautifulSoup

from ..config import config
from .registry import tool

# A realistic browser User-Agent + Accept headers. Sites commonly 403 the
# default "python-requests/x.y" UA as bot traffic; these get past most of them.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Module-level seam: tests patch this to inject responses without real network.
_get = requests.get


@tool
def fetch_url(url: str) -> str:
    """Fetch a web page and return its readable text. Use this to actually READ a
    page once you have its URL (e.g. a result from a web search): an article,
    documentation, a reference. Scripts and markup are stripped and very long
    pages are truncated. Returns an error string (not a crash) if the site is
    unreachable or blocks the request."""
    # Single-user local tool: fetching arbitrary hosts (incl. localhost / private
    # IPs) is an accepted risk here. On a shared or cloud host, restrict to
    # http(s) and reject private/link-local addresses before fetching.
    try:
        response = _get(url, headers=_HEADERS, timeout=15, allow_redirects=True)
        response.raise_for_status()
    except requests.HTTPError as exc:
        code = exc.response.status_code if exc.response is not None else "?"
        if code in (401, 403):
            return (
                f"{url} blocked the request (HTTP {code}) — the site refuses "
                f"automated access (bot protection). Try a different source."
            )
        return f"Fetching {url} failed: HTTP {code}."
    except requests.RequestException as exc:
        return f"Could not reach {url}: {exc}"

    soup = BeautifulSoup(response.text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    lines = [line.strip() for line in soup.get_text(separator="\n").splitlines() if line.strip()]
    content = "\n".join(lines)
    if not content:
        return f"{url} returned no readable text."
    if len(content) > config.output_char_cap:
        content = content[: config.output_char_cap] + "\n...[truncated]"
    return f"Content from {url}:\n\n{content}"

"""A read-only tool that fetches the readable text of a web page.

Reaches the network (unlike the workspace-confined file tools), so it is the
first tool that lets Vegapunk see outside the machine. Many sites reject the
default ``python-requests`` User-Agent with a 403, so we send realistic browser
headers; output is capped to protect the context window.
"""

from __future__ import annotations

import requests
from requests.compat import chardet
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
DOWNLOAD_LIMIT = 2 * 1024 * 1024


@tool
def fetch_url(url: str) -> str:
    """Fetch a web page and return its readable text. Use this to actually READ a
    page once you have its URL (e.g. a result from a web search): an article,
    documentation, a reference. Scripts and markup are stripped and very long
    pages are truncated. Downloads stop at 2 MiB of decompressed bytes and partial
    pages are labelled. Returns an error string (not a crash) if the site is
    unreachable or blocks the request."""
    # Single-user local tool: fetching arbitrary hosts (incl. localhost / private
    # IPs) is an accepted risk here. On a shared or cloud host, restrict to
    # http(s) and reject private/link-local addresses before fetching.
    response = None
    try:
        response = _get(url, headers=_HEADERS, timeout=15, allow_redirects=True, stream=True)
        response.raise_for_status()
        body = bytearray()
        complete = True
        for chunk in response.iter_content(chunk_size=65536):
            remaining = DOWNLOAD_LIMIT - len(body)
            body.extend(chunk[:remaining])
            if len(chunk) > remaining:
                complete = False
                break
        raw = bytes(body)
        # Detect only within the bounded body. response.apparent_encoding would
        # consume the unread stream and defeat the download limit.
        encoding = response.encoding or (chardet.detect(raw).get("encoding") if chardet else None) or "utf-8"
        try:
            text = raw.decode(encoding)
            decode_note = ""
        except UnicodeDecodeError:
            text = raw.decode(encoding, errors="replace")
            complete = False
            decode_note = "[Text decoding was lossy; source capture is incomplete]\n"
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
            complete = False
            decode_note = "[Declared text encoding was invalid or decoding was lossy; source capture is incomplete]\n"
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
    finally:
        if response is not None:
            response.close()

    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    lines = [line.strip() for line in soup.get_text(separator="\n").splitlines() if line.strip()]
    content = "\n".join(lines)
    if not content:
        return f"{url} returned no readable text." + (" Download was limited or decoding was incomplete." if not complete else "")
    from ..output_capture import record
    limit_note = (f"[Partial page: download limited to {DOWNLOAD_LIMIT} decompressed response bytes]\n" if len(body) == DOWNLOAD_LIMIT and not complete else "") + decode_note
    record(f"Content from {url}:\n\n{limit_note}{content}", complete=complete)
    if len(content) > config.output_char_cap:
        content = content[: config.output_char_cap] + "\n...[truncated]"
    return f"Content from {url}:\n\n{limit_note}{content}"

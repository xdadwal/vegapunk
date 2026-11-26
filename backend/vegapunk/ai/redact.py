"""Keeping secrets out of what is sent to the AI provider.

Everything that comes from a container (log lines above all) passes through `redact`
before it can reach the provider. It is best effort: it catches common shapes of secret,
not every possible one.

It is also bounded in time. Logs are untrusted input, so every line is cut before any
pattern runs and no pattern scans without a limit.
"""

import re

REDACTED = "[redacted]"
LINE_CAP = 2_000
SCRUB_CAP = 300

_PRIVATE_KEY = re.compile(
    r"-----BEGIN [A-Z ]{0,40}PRIVATE KEY-----.*?(?:-----END [A-Z ]{0,40}PRIVATE KEY-----|\Z)",
    re.DOTALL,
)
_AUTH_HEADER = re.compile(
    r"(?i)(authorization\s{0,5}[:=]\s{0,5}(?:basic|bearer|token)\s{1,5})\S{8,512}"
)
_BEARER = re.compile(r"(?i)\b(bearer\s{1,5})[A-Za-z0-9._~+/=\-]{8,512}")
_URL_CREDENTIALS = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]{0,20}://)[^\s/:@]{0,64}:[^\s/@]{1,128}@")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_\-]{5,2000}\.eyJ[A-Za-z0-9_\-]{5,2000}\.[A-Za-z0-9_\-]{5,2000}")
_TOKEN_SHAPES = re.compile(
    r"\bsk-[A-Za-z0-9_\-]{16,200}"
    r"|\bgh[pos]_[A-Za-z0-9]{20,200}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,200}"
    r"|\bxox[baprs]-[A-Za-z0-9\-]{10,200}"
    r"|\bAKIA[0-9A-Z]{16}\b"
)
_KEYWORD_VALUE = re.compile(
    r"(?i)((?:password|passwd|pwd|secret|token|api[_\-]?key|access[_\-]?key|private[_\-]?key|credential)"
    r"[\w.\-]{0,64}[\"']?\s{0,3}[:=]\s{0,3})"
    r"(\"[^\"\n]{0,500}\"|'[^'\n]{0,500}'|[^\s\"',;&]{1,500})"
)


def _keep_quotes(match: re.Match[str]) -> str:
    """Replace a value but keep its quote marks, so JSON-looking lines still read."""
    value = match.group(2)
    quote = value[0] if value[0] in "\"'" else ""
    return f"{match.group(1)}{quote}{REDACTED}{quote}"


def redact(text: str) -> str:
    """Replace secrets in `text` with `[redacted]`, keeping the rest readable."""
    text = "\n".join(line[:LINE_CAP] for line in text.split("\n"))
    text = _PRIVATE_KEY.sub(REDACTED, text)
    text = _AUTH_HEADER.sub(lambda m: m.group(1) + REDACTED, text)
    text = _BEARER.sub(lambda m: m.group(1) + REDACTED, text)
    text = _URL_CREDENTIALS.sub(lambda m: f"{m.group(1)}{REDACTED}@", text)
    text = _JWT.sub(REDACTED, text)
    text = _TOKEN_SHAPES.sub(REDACTED, text)
    text = _KEYWORD_VALUE.sub(_keep_quotes, text)
    return text


def limit_text(text: str, max_chars: int) -> str:
    """Keep the end of `text` within `max_chars`, saying how much was left out.

    The note counts towards the limit.
    """
    if len(text) <= max_chars:
        return text
    keep = max_chars
    note = ""
    for _ in range(4):  # the note's length depends on the number in it
        note = f"[earlier output omitted: {len(text) - keep} characters]\n"
        keep = max_chars - len(note)
    if keep <= 0:
        return text[-max_chars:] if max_chars > 0 else ""
    return note + text[-keep:]


def scrub_secret(text: str, secret: str | None) -> str:
    """Make provider or library error text safe to show.

    Removes the secret and, for a long one, its first six characters (logpose's own
    scrubbing leaves those in its marker), redacts anything else that looks secret, and
    cuts the result short.
    """
    if secret:
        text = text.replace(secret, REDACTED)
        if len(secret) >= 12:
            text = text.replace(secret[:6], REDACTED)
    return redact(text)[:SCRUB_CAP]

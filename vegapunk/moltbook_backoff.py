"""Credential-scoped request cooldowns; no credentials or server prose persisted."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import db


def until(key: str) -> str | None:
    """Return an active cooldown shared by all callers using this credential."""
    tag = hashlib.sha256(key.encode()).hexdigest()
    rows = db.query("SELECT until_at FROM moltbook_request_backoff WHERE credential_tag=?", (tag,))
    return rows[0][0] if rows and rows[0][0] > db.utcnow() else None


def record(key: str, retry_after: str | None) -> tuple[int, str]:
    """Persist a 429 delay, accepting delta-seconds or an HTTP date safely.

    Unknown values use one hour. Delays are bounded to 60 seconds through one
    year, matching existing reply cooldown bounds. Never shorten another limit.
    """
    stamp = db.utcnow()
    raw = retry_after.strip() if isinstance(retry_after, str) else ""
    seconds = 3600
    if len(raw) <= 256:
        if raw.isascii() and raw.isdigit() and len(raw) <= 10:
            seconds = int(raw)
        elif raw:
            try:
                date = parsedate_to_datetime(raw)
                if date.tzinfo is not None:
                    seconds = math.ceil((date.astimezone(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass  # Malformed server guidance gets the documented safe delay.
    seconds = max(60, min(seconds, 31536000))
    tag = hashlib.sha256(key.encode()).hexdigest()
    expiry = db.stamp_plus(stamp, seconds)
    with db.transaction(immediate=True) as conn:
        conn.execute("INSERT INTO moltbook_request_backoff VALUES (?,?) ON CONFLICT(credential_tag) "
                     "DO UPDATE SET until_at=MAX(until_at,excluded.until_at)", (tag, expiry))
        expiry = conn.execute("SELECT until_at FROM moltbook_request_backoff WHERE credential_tag=?", (tag,)).fetchone()[0]
    remaining = math.ceil((datetime.fromisoformat(expiry) - datetime.fromisoformat(stamp)).total_seconds())
    return remaining, expiry


def failure(key: str, *, authentication: bool = False) -> str:
    """Persist an automatic auth pause or capped consecutive failure backoff."""
    tag = hashlib.sha256(key.encode()).hexdigest()
    stamp = db.utcnow()
    with db.transaction(immediate=True) as conn:
        if authentication:
            seconds = 21600
        else:
            row = conn.execute("SELECT consecutive_failures FROM moltbook_request_health WHERE credential_tag=?", (tag,)).fetchone()
            count = min((row[0] if row else 0) + 1, 6)
            seconds = min(60 * 2 ** (count - 1), 1800)
            conn.execute("INSERT INTO moltbook_request_health VALUES (?,?) ON CONFLICT(credential_tag) "
                         "DO UPDATE SET consecutive_failures=excluded.consecutive_failures", (tag, count))
        conn.execute("INSERT INTO moltbook_request_backoff VALUES (?,?) ON CONFLICT(credential_tag) "
                     "DO UPDATE SET until_at=MAX(until_at,excluded.until_at)", (tag, db.stamp_plus(stamp, seconds)))
        return conn.execute("SELECT until_at FROM moltbook_request_backoff WHERE credential_tag=?", (tag,)).fetchone()[0]


def success(key: str) -> None:
    """Reset consecutive failures without shortening any concurrent cooldown."""
    tag = hashlib.sha256(key.encode()).hexdigest()
    db.execute("UPDATE moltbook_request_health SET consecutive_failures=0 WHERE credential_tag=?", (tag,))

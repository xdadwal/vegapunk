"""Timestamp formatting shared by the API, capture and watch code."""

from datetime import UTC, datetime


def z(value: datetime | str | None) -> str | None:
    """ISO-8601 UTC with a Z, from a datetime, an ISO string, or None."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def from_ts(ts: int) -> str:
    return z(datetime.fromtimestamp(ts, UTC))  # type: ignore[return-value]

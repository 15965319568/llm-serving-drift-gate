from __future__ import annotations

from datetime import UTC, datetime


def parse_time(value: str) -> datetime:
    """Parse the contract's UTC Z timestamp and reject ambiguous values."""
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"timestamp must use UTC Z format: {value!r}")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(UTC).replace(microsecond=0)


def iso(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


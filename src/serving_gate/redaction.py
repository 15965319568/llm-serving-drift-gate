from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

SECRET_KEYS = {"secret", "token", "api_key", "password", "signature", "credential", "authorization"}
_PAIR = re.compile(r"(?i)(token|secret|password|api[_-]?key|authorization)=([^&\s]+)")


def redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if not parsed.scheme or not parsed.netloc:
            return "[REDACTED_URL]"
        host = parsed.hostname or ""
        netloc = host + (f":{parsed.port}" if parsed.port else "")
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))
    except ValueError:
        return "[REDACTED_URL]"


def redact(value: Any, *, key: str = "") -> Any:
    lowered = key.lower()
    if lowered in SECRET_KEYS or any(part in lowered for part in ("secret", "token", "password", "signature")):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): redact(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(item, key=key) for item in value]
    if isinstance(value, str):
        if lowered in {"url", "endpoint"}:
            return redact_url(value)
        return _PAIR.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)
    return value


"""Hash user IDs and free-text fields before they hit the log."""

import hashlib

_SALT = "adaptivetouragent-v1"


def hash_id(value: str) -> str:
    """Stable per-process hash of an identifier."""
    digest = hashlib.sha256((_SALT + value).encode("utf-8")).hexdigest()
    return digest[:12]


def redact_text(value: str, max_len: int = 80) -> str:
    """Truncate and tag free-text so it is safe to log."""
    if not value:
        return ""
    snippet = value[:max_len]
    if len(value) > max_len:
        snippet += "…"
    return snippet

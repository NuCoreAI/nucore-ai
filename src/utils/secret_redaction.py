"""Process-wide registry of known secret values, and redaction applied at
the lowest-level logging/prompt-log write paths (see ``logger.py``'s
``RedactingFilter`` and ``prompt_log.py``'s own write path) -- so a secret
is registered once, wherever it first becomes known or is minted, and
every log line and prompt-log entry for the rest of this process's life
has it redacted automatically. No call site that merely logs something
needs to remember to redact it itself.
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Adds *value* to the registry. A no-op for ``None``/empty strings,
    so callers can pass an optional field straight through without an
    ``if`` guard of their own."""
    if value:
        with _lock:
            _secrets.add(value)


def redact(text: str) -> str:
    """Replaces every registered secret found in *text* with
    ``"[REDACTED]"``. Longest-first, so a secret that happens to be a
    substring of another registered value doesn't leave the longer one's
    remainder exposed."""
    with _lock:
        secrets = sorted(_secrets, key=len, reverse=True)
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    return text

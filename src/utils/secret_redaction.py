"""Process-wide registry of known secret values, and redaction applied at
the lowest-level logging/prompt-log write paths (see ``logger.py``'s
``RedactingFilter`` and ``prompt_log.py``'s own write path) -- so a secret
is registered once, wherever it first becomes known or is minted, and
every log line and prompt-log entry for the rest of this process's life
has it redacted automatically. No call site that merely logs something
needs to remember to redact it itself.
"""

from __future__ import annotations

import re
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


# --- Pattern-based scrubbing, for log output only -------------------------
#
# ``redact`` only knows values that were registered. A log line written
# before a secret was registered, or by a library that logs a raw
# configuration payload (udi_interface logs every Polyglot message it
# receives, including the plugin's custom parameters with provider API keys),
# would otherwise carry it through. ``scrub`` additionally recognizes secrets
# by shape and by field name.
#
# Deliberately NOT part of ``redact``: ``redact`` also runs on persisted
# session history, where rewriting something that merely looks like a secret
# (generated plugin code with ``api_key = "..."``) would corrupt the stored
# conversation and change what is replayed to the model.

_REDACTED = "[REDACTED]"

_KEY_SHAPES = re.compile(
    r"\b(?:sk-ant-[A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9_\-]{20,}|xai-[A-Za-z0-9]{20,}"
    r"|gsk_[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_\-]{30,})"
)
_BEARER = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=\-]{16,}")
# A quoted value after a sensitive field name, in JSON, a Python repr, or the
# backslash-escaped JSON a logger shows for a JSON string inside a string.
# Quotes are required so unquoted code (``client_secret = self.get()``) is left alone.
_SENSITIVE_FIELD = re.compile(
    r"(?i)(\b(?:api[_-]?key|x-api-key|client_secret|access_token|refresh_token|id_token"
    r"|password|passwd|secret|authorization)\b\\*[\"']?\s*[:=]\s*\\*[\"'])"
    r"(?!(?:null|none|true|false)\b)[^\"'\\\s,}\[\]]+"
)
_URL_PARAM = re.compile(r"(?i)([?&](?:api_key|apikey|access_token|token|password|client_secret)=)[^&\s\"']+")


def scrub(text: str) -> str:
    """``redact`` plus shape- and field-name-based scrubbing -- see above.
    Never raises; returns *text* unchanged on any failure."""
    try:
        text = redact(text)
        text = _KEY_SHAPES.sub(_REDACTED, text)
        text = _BEARER.sub(r"\1" + _REDACTED, text)
        text = _SENSITIVE_FIELD.sub(r"\1" + _REDACTED, text)
        return _URL_PARAM.sub(r"\1" + _REDACTED, text)
    except Exception:
        return text

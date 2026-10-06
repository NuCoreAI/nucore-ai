"""Shared secret-leak guard for plugin_authoring's outbound-call tools
(handlers/discovery.py) and its scaffold-writing tool (handlers/scaffold.py):
both need the exact same "does this text contain a configured secret
value" check, used to refuse an action before it happens -- never to
silently redact and proceed. Written once here so Phase 4 reuses it
instead of re-deriving the same check (see path_confinement.py for the
same pattern applied to filesystem paths).
"""

from __future__ import annotations

# server_entry.json's oauth.client_id/client_secret are always this literal
# string at generation time (impl_plan.md's secret-placeholder principle,
# design/developers/plugin_authoring_p4_impl.md's "OAuth secrets are
# placeholders at generation time, always") -- a real value never belongs in
# a generated file; it's filled in post-install via configure_plugin(key=
# "oauth"). Any other literal value in that field is refused outright.
OAUTH_PLACEHOLDER = "SET_VIA_CONFIGURE_PLUGIN_OAUTH_KEY"


def contains_secret(text: str, secret_values: list[str]) -> str | None:
    """Returns the first configured secret value found literally inside
    *text*, or ``None``. Used to refuse an outbound call/write before it's
    made -- never to silently redact and send/write a mangled result."""
    for secret in secret_values:
        if secret and secret in text:
            return secret
    return None


def find_secret(texts: list[str], secret_values: list[str]) -> str | None:
    """Same as ``contains_secret``, scanning several pieces of text (e.g.
    every LLM-authored file plugin_authoring is about to write) and
    returning the first hit across all of them, or ``None``."""
    for text in texts:
        hit = contains_secret(text, secret_values)
        if hit:
            return hit
    return None


def redact_secrets(text: str, secret_values: list[str]) -> str:
    """For anything that *does* get logged -- unlike ``contains_secret``,
    this is a best-effort cleanup, not a gate."""
    for secret in secret_values:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text

"""secret_redaction's process-wide registry/redact() -- the primitive
``logger.py``'s ``RedactingFilter`` and ``prompt_log.py``'s write path both
build on.
"""

from __future__ import annotations

import pytest

from utils import secret_redaction


@pytest.fixture(autouse=True)
def _restore_secret_registry():
    saved = set(secret_redaction._secrets)
    try:
        yield
    finally:
        secret_redaction._secrets.clear()
        secret_redaction._secrets.update(saved)


def test_register_then_redact_replaces_every_occurrence():
    secret_redaction.register_secret("sk-abc123")
    text = "key=sk-abc123, again sk-abc123"
    assert secret_redaction.redact(text) == "key=[REDACTED], again [REDACTED]"


def test_unregistered_text_passes_through_unchanged():
    assert secret_redaction.redact("nothing secret here") == "nothing secret here"


def test_register_secret_is_a_noop_for_none_and_empty_string():
    secret_redaction.register_secret(None)
    secret_redaction.register_secret("")
    assert secret_redaction.redact("") == ""
    # No exception, and nothing spurious got registered.
    assert "" not in secret_redaction._secrets


def test_substring_secret_does_not_leave_a_partial_overlap_exposed():
    # "secret123" is itself a registered value, and "123" is also
    # separately registered -- redacting "123" first would leave "[REDACTED]"
    # embedded oddly, but redacting longest-first means the full value is
    # consumed before the shorter substring ever gets a chance to match.
    secret_redaction.register_secret("secret123")
    secret_redaction.register_secret("123")
    assert secret_redaction.redact("token=secret123") == "token=[REDACTED]"


def test_two_independently_registered_secrets_are_both_redacted():
    secret_redaction.register_secret("alpha-secret")
    secret_redaction.register_secret("beta-secret")
    result = secret_redaction.redact("alpha-secret and beta-secret")
    assert "alpha-secret" not in result
    assert "beta-secret" not in result
    assert result.count("[REDACTED]") == 2

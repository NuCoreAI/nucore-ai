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


_FAKE_ANTHROPIC = "sk-ant-api03-FAKEFAKEFAKEFAKEFAKEFAKE12345678"


def test_scrub_catches_an_unregistered_key_by_its_shape():
    assert _FAKE_ANTHROPIC not in secret_redaction.scrub(f"calling with {_FAKE_ANTHROPIC} now")
    assert "AIza" not in secret_redaction.scrub("url?key=AIza" + "a" * 35)
    assert "xai-" not in secret_redaction.scrub("k=xai-" + "b" * 30)


def test_scrub_handles_the_escaped_json_a_logger_shows_for_polyglot_custom_parameters():
    text = 'message {"value": "{\\"api_key\\": \\"hunter2hunter2\\", \\"max_tokens\\": 64000, \\"stream\\": true}"}'
    scrubbed = secret_redaction.scrub(text)
    assert "hunter2hunter2" not in scrubbed
    assert '\\"max_tokens\\": 64000' in scrubbed and '\\"stream\\": true' in scrubbed


def test_scrub_handles_dict_reprs_bearer_headers_and_url_parameters():
    scrubbed = secret_redaction.scrub(
        "{'api_key': 'abcdef123456'} Authorization: Bearer abcdefghijklmnopqrstuvwx https://h/x?token=abc123&a=1"
    )
    assert "abcdef123456" not in scrubbed and "abcdefghijklmnopqrstuvwx" not in scrubbed and "abc123" not in scrubbed
    assert "a=1" in scrubbed


def test_scrub_leaves_ordinary_text_code_and_empty_values_alone():
    for text in (
        "max_tokens: 64000 and history_token_budget: 120000",
        'self.client_secret = self.Parameters["client_secret"]',
        "client_secret = os.environ.get('X')",
        '{"api_key": null, "stream": false}',
        "{'api_key': None}",
        "nothing secret here",
    ):
        assert secret_redaction.scrub(text) == text


def test_scrub_is_idempotent_and_also_applies_the_registry():
    secret_redaction.register_secret("plainsecret")
    once = secret_redaction.scrub(f'"api_key": "abcdef123456" plainsecret {_FAKE_ANTHROPIC}')
    assert "plainsecret" not in once and secret_redaction.scrub(once) == once


def test_redact_alone_stays_registry_only_so_persisted_history_is_never_rewritten():
    code = 'client = Client(api_key="not-registered-123456")'
    assert secret_redaction.redact(code) == code

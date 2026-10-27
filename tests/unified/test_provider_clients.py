"""build_provider_clients_from_runtime_config registers every resolved LLM
provider API key as a secret -- the one choke point every profile's key
(inline, or via its ``"${ENV_VAR}"``/raw-env-var fallback) actually flows
through, so this is the only place that registration needs to happen.
"""

from __future__ import annotations

from utils import secret_redaction
from unified.provider_clients import build_provider_clients_from_runtime_config

import pytest


@pytest.fixture(autouse=True)
def _restore_secret_registry():
    saved = set(secret_redaction._secrets)
    try:
        yield
    finally:
        secret_redaction._secrets.clear()
        secret_redaction._secrets.update(saved)


def test_inline_api_key_is_registered():
    runtime_config = {"nucore_runtime": {"unified": {"provider": "claude", "api_key": "sk-ant-inline-secret"}}}
    build_provider_clients_from_runtime_config(runtime_config, env={})
    assert "sk-ant-inline-secret" in secret_redaction._secrets


def test_env_fallback_api_key_is_registered():
    runtime_config = {"nucore_runtime": {"unified": {"provider": "openai"}}}
    build_provider_clients_from_runtime_config(runtime_config, env={"OPENAI_API_KEY": "sk-from-env"})
    assert "sk-from-env" in secret_redaction._secrets


def test_env_placeholder_resolves_and_registers_the_real_value_not_the_placeholder():
    runtime_config = {
        "nucore_runtime": {"unified": {"provider": "grok", "api_key": "${XAI_API_KEY}"}}
    }
    build_provider_clients_from_runtime_config(runtime_config, env={"XAI_API_KEY": "xai-real-secret"})
    assert "xai-real-secret" in secret_redaction._secrets
    assert "${XAI_API_KEY}" not in secret_redaction._secrets


def test_llamacpp_sentinel_no_key_is_not_registered():
    runtime_config = {"nucore_runtime": {"unified": {"provider": "llamacpp"}}}
    build_provider_clients_from_runtime_config(runtime_config, env={})
    assert "no-key" not in secret_redaction._secrets

from __future__ import annotations

from typing import Any

from .provider_clients import build_provider_clients_from_runtime_config
from .provider_dispatch_adapter import ProviderDispatchLLMAdapter


def build_default_dispatch_adapter(
    runtime_config: dict[str, Any],
    *,
    extra_clients: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> ProviderDispatchLLMAdapter:
    clients = build_provider_clients_from_runtime_config(runtime_config, env=env)
    if extra_clients:
        for provider, client in extra_clients.items():
            clients[str(provider).strip().lower()] = client

    if not clients:
        raise ValueError(
            "No provider clients could be created from runtime config. "
            "Add API keys or pass extra_clients."
        )

    default_llm_key = runtime_config.get("default_llm")
    supported_llms = runtime_config.get("supported_llms", {})
    default_provider = None
    if default_llm_key and isinstance(supported_llms, dict):
        cfg = supported_llms.get(default_llm_key, {})
        if isinstance(cfg, dict):
            default_provider = cfg.get("provider") or cfg.get("llm")

    return ProviderDispatchLLMAdapter(clients=clients, default_provider=default_provider)

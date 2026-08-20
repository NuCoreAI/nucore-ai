from __future__ import annotations

import os
from typing import Any

from .adapters import (
    ClaudeAdapter,
    GeminiAdapter,
    GrokAdapter,
    LlamaCppAdapter,
    OpenAIAdapter,
)


def build_provider_clients_from_runtime_config(
    runtime_config: dict[str, Any],
    *,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Instantiate LLM adapter clients from runtime profiles.

    Iterates every entry in ``runtime_config["nucore_runtime"]`` and creates the
    appropriate adapter for each recognised provider.  Entries whose API key
    cannot be resolved (from the config itself or the environment) are silently
    skipped so that a partially-configured deployment still starts up cleanly.

    The ``llama.cpp`` provider is the only one that does not require a real API
    key — it defaults to the placeholder string ``"no-key"`` when none is set.

    Args:
        runtime_config: Runtime configuration dict that contains
                ``runtime_config["nucore_runtime"]`` as a mapping of
                profile key → per-LLM config dict. Each config dict is
                expected to contain:

                * ``provider`` — provider identifier string.
                * ``url``       — optional custom base URL.
                * ``api_key``   — optional inline API key.

        env:            Optional environment variable mapping used for API key
                        look-ups.  Defaults to a snapshot of ``os.environ`` when
                        ``None``.  Pass an explicit dict in tests to avoid reading
                        real environment variables.

    Returns:
        Dict mapping canonical provider name (e.g. ``"openai"``, ``"claude"``,
        ``"grok"``, ``"llama.cpp"``, ``"gemini"``) to the corresponding adapter
        instance.  Only providers whose keys were successfully resolved are
        included.
    """
    # Snapshot the environment once so all key lookups are consistent.
    env_map = env or dict(os.environ)
    runtime_profiles = dict(runtime_config.get("nucore_runtime", {}))

    clients: dict[str, Any] = {}
    for llm_key, llm_cfg in runtime_profiles.items():
        if not isinstance(llm_cfg, dict):
            continue

        provider = str(llm_cfg.get("provider") or "").strip().lower()
        profile_key = str(llm_key or "").strip().lower()
        if not profile_key:
            continue
        base_url = llm_cfg.get("url")
        # API key is provided directly in profile config (or env fallback below).
        api_key = llm_cfg.get("api_key")
        if isinstance(api_key, str) and api_key.startswith("${") and api_key.endswith("}"):
            # Support "${ENV_VAR}" syntax for explicit environment variable references in config.
            env_var_name = api_key[2:-1].strip()
            api_key = env_map.get(env_var_name, "")

        if provider == "openai":
            key = api_key or env_map.get("OPENAI_API_KEY")
            if key:
                adapter = OpenAIAdapter(api_key=key, base_url=base_url)
                clients[profile_key] = adapter
                clients.setdefault("openai", adapter)
        elif provider in {"claude", "anthropic"}:
            key = api_key or env_map.get("ANTHROPIC_API_KEY")
            if key:
                adapter = ClaudeAdapter(api_key=key, base_url=base_url)
                clients[profile_key] = adapter
                clients.setdefault("claude", adapter)
        elif provider in {"grok", "xai", "x.ai"}:
            key = api_key or env_map.get("XAI_API_KEY") or env_map.get("GROK_API_KEY")
            if key:
                adapter = GrokAdapter(api_key=key, base_url=base_url)
                clients[profile_key] = adapter
                clients.setdefault("grok", adapter)
        elif provider in {"llama.cpp", "llamacpp", "llama_cpp"}:
            # llama.cpp servers typically run without authentication; fall back
            # to a sentinel value so the adapter can still be constructed.
            key = api_key or env_map.get("LLAMACPP_API_KEY") or "no-key"
            adapter = LlamaCppAdapter(api_key=key, base_url=base_url)
            clients[profile_key] = adapter
            clients.setdefault("llama.cpp", adapter)
        elif provider in {"gemini", "google"}:
            key = api_key or env_map.get("GEMINI_API_KEY")
            if key:
                adapter = GeminiAdapter(api_key=key, base_url=base_url)
                clients[profile_key] = adapter
                clients.setdefault("gemini", adapter)

    return clients

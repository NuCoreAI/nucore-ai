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
    """Instantiate LLM adapter clients from the ``supported_llms`` section of a runtime config.

    Iterates every entry in ``runtime_config["supported_llms"]`` and creates the
    appropriate adapter for each recognised provider.  Entries whose API key
    cannot be resolved (from the config itself or the environment) are silently
    skipped so that a partially-configured deployment still starts up cleanly.

    The ``llama.cpp`` provider is the only one that does not require a real API
    key — it defaults to the placeholder string ``"no-key"`` when none is set.

    Args:
        runtime_config: Parsed ``runtime_config.json`` dict.  The function reads
                        ``runtime_config["supported_llms"]``, which should be a
                        mapping of arbitrary alias → per-LLM config dict.  Each
                        config dict is expected to contain:

                        * ``provider`` (or ``llm``) — provider identifier string.
                        * ``url``                   — optional custom base URL.
                        * ``api_key``               — optional inline API key.
                        * ``params.api_key``        — alternative inline key location.

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
    supported_llms = dict(runtime_config.get("supported_llms", {}))

    clients: dict[str, Any] = {}
    for _, llm_cfg in supported_llms.items():
        if not isinstance(llm_cfg, dict):
            continue

        # Normalise provider name; accept both "provider" and legacy "llm" keys.
        provider = str(llm_cfg.get("provider") or llm_cfg.get("llm") or "").strip().lower()
        base_url = llm_cfg.get("url")
        # Inline key can live at the top level or nested inside "params".
        params = llm_cfg.get("params", {}) if isinstance(llm_cfg.get("params"), dict) else {}
        api_key = llm_cfg.get("api_key") or params.get("api_key")

        if provider == "openai":
            key = api_key or env_map.get("OPENAI_API_KEY")
            if key:
                clients["openai"] = OpenAIAdapter(api_key=key, base_url=base_url)
        elif provider in {"claude", "anthropic"}:
            key = api_key or env_map.get("ANTHROPIC_API_KEY")
            if key:
                clients["claude"] = ClaudeAdapter(api_key=key, base_url=base_url)
        elif provider in {"grok", "xai", "x.ai"}:
            key = api_key or env_map.get("XAI_API_KEY") or env_map.get("GROK_API_KEY")
            if key:
                clients["grok"] = GrokAdapter(api_key=key, base_url=base_url)
        elif provider in {"llama.cpp", "llamacpp", "llama_cpp"}:
            # llama.cpp servers typically run without authentication; fall back
            # to a sentinel value so the adapter can still be constructed.
            key = api_key or env_map.get("LLAMACPP_API_KEY") or "no-key"
            clients["llama.cpp"] = LlamaCppAdapter(api_key=key, base_url=base_url)
        elif provider in {"gemini", "google"}:
            key = api_key or env_map.get("GEMINI_API_KEY")
            if key:
                clients["gemini"] = GeminiAdapter(api_key=key, base_url=base_url)

    return clients

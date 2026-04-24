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
    env_map = env or dict(os.environ)
    supported_llms = dict(runtime_config.get("supported_llms", {}))

    clients: dict[str, Any] = {}
    for _, llm_cfg in supported_llms.items():
        if not isinstance(llm_cfg, dict):
            continue

        provider = str(llm_cfg.get("provider") or llm_cfg.get("llm") or "").strip().lower()
        base_url = llm_cfg.get("url")
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
            key = api_key or env_map.get("LLAMACPP_API_KEY") or "no-key"
            clients["llama.cpp"] = LlamaCppAdapter(api_key=key, base_url=base_url)
        elif provider in {"gemini", "google"}:
            key = api_key or env_map.get("GEMINI_API_KEY")
            if key:
                clients["gemini"] = GeminiAdapter(api_key=key, base_url=base_url)

    return clients

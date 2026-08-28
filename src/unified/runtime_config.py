"""Runtime-profile JSON loading/normalization -- extracted from the classic
``intent_handler/runtime.py`` (now retired). Purely config parsing/
normalization with no dependency on any router/intent-handler class, so it
carried over unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .stream_handler import StreamHandler

_PROVIDER_CAPABILITIES: dict[str, dict[str, Any]] = {
    # Anthropic's SDK accepts a dedicated system prompt and we already map
    # system-role messages onto that field inside the adapter.
    "claude": {"supports_system_role": True},
    "anthropic": {"supports_system_role": True},
    "openai": {"supports_system_role": True},
    "gpt": {"supports_system_role": True},
    "gemini": {"supports_system_role": True},
    "google": {"supports_system_role": True},
    "grok": {"supports_system_role": True},
    "xai": {"supports_system_role": True},
    "x.ai": {"supports_system_role": True},
    "llamacpp": {"supports_system_role": True},
    "llama_cpp": {"supports_system_role": True},
    "llama.cpp": {"supports_system_role": True},
}


def _normalize_provider_name(provider: str | None) -> str:
    value = str(provider or "").strip().lower()
    if value == "anthropic":
        return "claude"
    if value == "gpt":
        return "openai"
    if value in {"google"}:
        return "gemini"
    if value in {"xai", "x.ai"}:
        return "grok"
    if value in {"llamacpp", "llama_cpp"}:
        return "llama.cpp"
    return value


def _coerce_runtime_profile(
    profile_name: str,
    payload: dict[str, Any],
    *,
    stream_handler: StreamHandler | None,
    force_stream: bool | None = None,
) -> dict[str, Any]:
    """Normalize one ``nucore_runtime`` profile into dispatch-ready shape.

    Whether this profile actually streams is decided by its own ``stream``
    key in ``runtime_config.example.json`` -- e.g. the ``unified`` profile
    opts in, others don't -- unless ``force_stream`` (a CLI-level
    ``--stream``/``--no-stream`` override) is set, in which case it wins for
    every profile uniformly. Either way, streaming only actually happens when
    a real ``stream_handler`` was also supplied by the caller.
    """
    provider = _normalize_provider_name(payload.get("provider"))
    if not provider:
        raise ValueError(f"nucore_runtime.{profile_name} must define a non-empty 'provider'")

    capabilities = _PROVIDER_CAPABILITIES.get(provider, {})
    result: dict[str, Any] = {
        "provider": provider,
        "model": payload.get("model"),
        "api_key": payload.get("api_key"),
        "url": payload.get("url"),
        "max_turns": int(payload.get("max_turns", 20)),
        "temperature": payload.get("temperature"),
        "max_tokens": payload.get("max_tokens"),
        "reasoning_effort": payload.get("reasoning_effort"),
        "supports_system_role": bool(
            payload.get("supports_system_role", capabilities.get("supports_system_role", True))
        ),
    }
    wants_stream = bool(payload.get("stream", False)) if force_stream is None else force_stream
    if wants_stream and stream_handler is not None:
        result["stream"] = True
        result["stream_handler"] = stream_handler.handle_stream_chunk
    else:
        result["stream"] = False
    return result


def _load_runtime_config(
    path: str,
    stream_handler: StreamHandler,
    *,
    force_stream: bool | None = None,
) -> dict[str, Any]:
    """Load and normalize CLI-provided runtime profiles.

    Expected file format:

    {
      "max_iterations": 8,
      "preferences_dir": null,
      "nucore_runtime": {
        "default": {...},
        "router": {...},
        "intent_name": {...}
      }
    }
    """
    if not path:
        raise ValueError("A runtime profile JSON path is required")

    runtime_profile_path = Path(path).expanduser().resolve()
    if not runtime_profile_path.exists() or not runtime_profile_path.is_file():
        raise FileNotFoundError(f"Runtime profile file not found: {runtime_profile_path}")

    with runtime_profile_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ValueError("Runtime profile must be a JSON object at top level")

    raw_runtime = payload.get("nucore_runtime")
    if not isinstance(raw_runtime, dict):
        raise ValueError("Runtime profile must contain an object key 'nucore_runtime'")

    raw_default = raw_runtime.get("default")
    if not isinstance(raw_default, dict):
        raise ValueError("nucore_runtime.default must be an object")

    default_profile = _coerce_runtime_profile(
        "default", raw_default, stream_handler=stream_handler, force_stream=force_stream
    )

    supported_llms: dict[str, dict[str, Any]] = {"default": default_profile}
    normalized_profiles: dict[str, dict[str, Any]] = {"default": default_profile}

    raw_router = raw_runtime.get("router")
    if raw_router is not None:
        if not isinstance(raw_router, dict):
            raise ValueError("nucore_runtime.router must be an object when provided")
        router_profile = _coerce_runtime_profile(
            "router", raw_router, stream_handler=stream_handler, force_stream=force_stream
        )
        supported_llms["router"] = router_profile
        normalized_profiles["router"] = router_profile

    for profile_name, profile_payload in raw_runtime.items():
        if profile_name in {"default", "router"}:
            continue
        if not isinstance(profile_payload, dict):
            raise ValueError(f"nucore_runtime.{profile_name} must be an object")
        normalized_profile = _coerce_runtime_profile(
            profile_name,
            profile_payload,
            stream_handler=stream_handler,
            force_stream=force_stream,
        )
        supported_llms[profile_name] = normalized_profile
        normalized_profiles[profile_name] = normalized_profile

    default_max_turns = int(default_profile.get("max_turns", 20))

    configured_max_iterations = payload.get("max_iterations")
    if configured_max_iterations is not None and not isinstance(configured_max_iterations, int):
        raise ValueError("max_iterations must be an integer when provided")

    configured_preferences_dir = payload.get("preferences_dir")
    if configured_preferences_dir is not None and not isinstance(configured_preferences_dir, str):
        raise ValueError("preferences_dir must be a string when provided")

    configured_history_token_budget = payload.get("history_token_budget")
    if configured_history_token_budget is not None and not isinstance(configured_history_token_budget, int):
        raise ValueError("history_token_budget must be an integer when provided")

    return {
        "nucore_runtime": normalized_profiles,
        "supported_llms": supported_llms,
        "max_iterations": int(configured_max_iterations) if configured_max_iterations is not None else 8,
        # No default -- None means preferences are simply unavailable for
        # this installation (see unified.preferences.preference_store.get_store).
        "preferences_dir": configured_preferences_dir,
        "default_llm": "default",
        "router_llm": "router" if "router" in supported_llms else "default",
        "default_max_turns": default_max_turns,
        "provider_capabilities": dict(_PROVIDER_CAPABILITIES),
        # Compaction trigger for UnifiedRuntime.handle_query's conversation
        # history -- see history_compaction.maybe_compact_history. Bounds
        # estimated token size, not turn count (default_max_turns above
        # already bounds that separately).
        "history_token_budget": (
            int(configured_history_token_budget) if configured_history_token_budget is not None else 20000
        ),
    }

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
) -> dict[str, Any]:
    """Normalize one ``nucore_runtime`` profile into dispatch-ready shape.

    Whether this profile actually streams is decided entirely by its own
    ``stream`` key in ``runtime_config.example.json`` -- e.g. the ``unified``
    profile opts in, others don't. Streaming only actually happens when a
    real ``stream_handler`` was also supplied by the caller.

    ``max_iterations``/``max_turns``/``history_token_budget``/
    ``fabrication_guard_mode``/``max_fabrication_retries`` live here, per
    profile, rather than as top-level config keys -- see
    design/developers/merged-toolsets.md: different tool sets (``unified``
    vs. ``plugin_authoring``) genuinely need different values for all five,
    the same way they need different models. ``enabled`` (default ``True``)
    and ``plugin_output_root`` (meaningful only for the ``plugin_authoring``
    profile) are the gate on whether this tool set is reachable at all --
    see that same doc.
    """
    provider = _normalize_provider_name(payload.get("provider"))
    if not provider:
        raise ValueError(f"nucore_runtime.{profile_name} must define a non-empty 'provider'")

    capabilities = _PROVIDER_CAPABILITIES.get(provider, {})
    cache_ttl = payload.get("cache_ttl")
    if cache_ttl is not None and cache_ttl not in ("5m", "1h"):
        raise ValueError(f'nucore_runtime.{profile_name}.cache_ttl must be "5m" or "1h", got {cache_ttl!r}')

    fabrication_guard_mode = payload.get("fabrication_guard_mode", "log")
    if fabrication_guard_mode not in ("off", "log", "block"):
        raise ValueError(
            f'nucore_runtime.{profile_name}.fabrication_guard_mode must be "off", "log", or '
            f"\"block\", got {fabrication_guard_mode!r}"
        )

    max_iterations = payload.get("max_iterations", 8)
    if not isinstance(max_iterations, int):
        raise ValueError(f"nucore_runtime.{profile_name}.max_iterations must be an integer")

    history_token_budget = payload.get("history_token_budget", 20000)
    if not isinstance(history_token_budget, int):
        raise ValueError(f"nucore_runtime.{profile_name}.history_token_budget must be an integer")

    max_fabrication_retries = payload.get("max_fabrication_retries", 1)
    if not isinstance(max_fabrication_retries, int):
        raise ValueError(f"nucore_runtime.{profile_name}.max_fabrication_retries must be an integer")

    result: dict[str, Any] = {
        "provider": provider,
        "model": payload.get("model"),
        "api_key": payload.get("api_key"),
        "url": payload.get("url"),
        "max_turns": int(payload.get("max_turns", 20)),
        "temperature": payload.get("temperature"),
        "max_tokens": payload.get("max_tokens"),
        "reasoning_effort": payload.get("reasoning_effort"),
        # Prompt-cache TTL for the static prefix (tools + static system
        # sections); "1h" costs 2x to write vs 1.25x for "5m", so it only
        # pays off when conversations are typically 5-60 minutes apart.
        "cache_ttl": cache_ttl,
        "supports_system_role": bool(
            payload.get("supports_system_role", capabilities.get("supports_system_role", True))
        ),
        "enabled": bool(payload.get("enabled", True)),
        "plugin_output_root": payload.get("plugin_output_root"),
        "max_iterations": max_iterations,
        "history_token_budget": history_token_budget,
        "fabrication_guard_mode": fabrication_guard_mode,
        "max_fabrication_retries": max_fabrication_retries,
    }
    wants_stream = bool(payload.get("stream", False))
    if wants_stream and stream_handler is not None:
        result["stream"] = True
        result["stream_handler"] = stream_handler.handle_stream_chunk
    else:
        result["stream"] = False
    return result


def _load_runtime_config(
    path: str,
    stream_handler: StreamHandler,
) -> dict[str, Any]:
    """Load and normalize CLI-provided runtime profiles.

    Expected file format (see design/developers/merged-toolsets.md):

    {
      "max_tool_set_switches_per_turn": 1,
      "search_engine": "brave",
      "search_engine_api_key": "${SEARCH_ENGINE_API_KEY}",
      "nucore_runtime": {
        "unified": {"enabled": true, ...},
        "plugin_authoring": {"enabled": true, "plugin_output_root": "...", ...}
      }
    }

    At least one profile must be present and ``enabled``; which profiles
    exist and are enabled is the *only* gate on tool-set availability --
    there is no longer a ``--tool-set``/``--plugin-output-root`` CLI flag.
    Most other settings that once had a CLI-level override
    (``--stream``/``--no-stream``, ``--max-iterations``, ``--search-engine``)
    were removed the same way: this file is the only source of truth for
    them, so there's nothing left to override.

    ``preferences_dir`` and the prompt-log settings are the deliberate
    exception, in the other direction: they moved *out* of this file and are
    CLI-only now (``--preferences-dir``, ``--prompt-log-file``), never read
    from here even if present. The distinction: this file holds runtime
    parameters a customer could reasonably supply (model, temperature,
    which tool sets are enabled, ...); where on disk preferences/logs get
    written is a host/deployment concern the system -- whoever launches the
    process -- controls, not whoever supplied the config file.

    An unrecognized key (anything not listed above or in a profile's own
    fields) is never an error -- it's silently ignored, which is also how a
    documentation-only key survives in a config file despite JSON having no
    comment syntax (see ``runtime_config.example.json``'s per-profile
    ``_notes`` for an example).
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
    if not isinstance(raw_runtime, dict) or not raw_runtime:
        raise ValueError("Runtime profile must contain a non-empty object key 'nucore_runtime'")

    supported_llms: dict[str, dict[str, Any]] = {}
    normalized_profiles: dict[str, dict[str, Any]] = {}
    for profile_name, profile_payload in raw_runtime.items():
        if not isinstance(profile_payload, dict):
            raise ValueError(f"nucore_runtime.{profile_name} must be an object")
        normalized_profile = _coerce_runtime_profile(
            profile_name,
            profile_payload,
            stream_handler=stream_handler,
        )
        supported_llms[profile_name] = normalized_profile
        normalized_profiles[profile_name] = normalized_profile

    enabled_profiles = [name for name, p in normalized_profiles.items() if p.get("enabled")]
    if not enabled_profiles:
        raise ValueError(
            "at least one nucore_runtime profile must have 'enabled' true (or omit 'enabled', "
            "which defaults to true)"
        )

    plugin_authoring_profile = normalized_profiles.get("plugin_authoring")
    if (
        plugin_authoring_profile is not None
        and plugin_authoring_profile.get("enabled")
        and not plugin_authoring_profile.get("plugin_output_root")
    ):
        raise ValueError(
            "nucore_runtime.plugin_authoring requires 'plugin_output_root' when enabled -- the "
            "tool set refuses to start without an allowed output root"
        )

    # "unified" is the preferred default active tool set for a new connection
    # when both are enabled (today's behavior); otherwise whichever single
    # profile is enabled (see merged-toolsets.md's "enabled flag" section).
    default_profile_name = "unified" if "unified" in enabled_profiles else enabled_profiles[0]
    default_max_turns = int(normalized_profiles[default_profile_name].get("max_turns", 20))

    configured_max_switches = payload.get("max_tool_set_switches_per_turn")
    if configured_max_switches is not None and not isinstance(configured_max_switches, int):
        raise ValueError("max_tool_set_switches_per_turn must be an integer when provided")

    # search_engine/search_engine_api_key are global (not per-profile) --
    # either tool set's discovery tools may use them, not just
    # plugin_authoring's (see merged-toolsets.md). The key follows the same
    # "${ENV_VAR}" convention api_key already uses -- resolved later via
    # provider_clients.resolve_env_placeholder, never read from the file
    # directly as a literal secret.
    configured_search_engine = payload.get("search_engine")
    if configured_search_engine is not None and configured_search_engine not in ("brave", "tavily"):
        raise ValueError(f'search_engine must be "brave" or "tavily", got {configured_search_engine!r}')

    return {
        "nucore_runtime": normalized_profiles,
        "supported_llms": supported_llms,
        "enabled_profiles": enabled_profiles,
        "default_profile_name": default_profile_name,
        "default_max_turns": default_max_turns,
        "provider_capabilities": dict(_PROVIDER_CAPABILITIES),
        # Bounds how many times one turn may chain from one tool set's
        # AgenticLoop into the other's after a switch-tool call -- see
        # design/developers/merged-toolsets.md's "Switch-count cap".
        "max_tool_set_switches_per_turn": configured_max_switches if configured_max_switches is not None else 1,
        # No default -- None means no engine configured here; absence just
        # disables the search_web tool for whichever tool set would have
        # used it.
        "search_engine": configured_search_engine,
        "search_engine_api_key": payload.get("search_engine_api_key"),
    }

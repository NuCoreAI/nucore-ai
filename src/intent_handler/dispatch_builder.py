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
    """Construct a :class:`ProviderDispatchLLMAdapter` from a runtime config dict.

    This is the primary factory used by the intent runtime entrypoint.  It
    delegates provider client instantiation to
    :func:`~provider_clients.build_provider_clients_from_runtime_config`, then
    wraps all resulting clients in a dispatch adapter that can route each
    request to the correct provider at call time.

    Args:
        runtime_config: Parsed ``runtime_config.json`` dict.  Expected keys:

            * ``supported_llms`` — mapping of LLM alias → config dict, each
              with at minimum ``provider`` (or ``llm``), ``model``, and
              optionally ``api_key`` / ``url``.
            * ``default_llm``    — alias key used to resolve the default
              provider name forwarded to the adapter.

        extra_clients:  Optional dict of ``provider_name → adapter instance``
                        to merge on top of the clients built from config.
                        Useful for injecting mock adapters in tests or for
                        providers not yet supported by the auto-builder.
        env:            Optional environment variable override dict passed
                        through to the client builder (defaults to
                        ``os.environ`` when ``None``).

    Returns:
        A configured :class:`ProviderDispatchLLMAdapter` ready to handle
        ``generate`` calls for any of the instantiated providers.

    Raises:
        ValueError: If no provider clients could be created (missing API keys
                    and no ``extra_clients`` supplied).
    """
    # Build provider-keyed client dict from the runtime config.
    clients = build_provider_clients_from_runtime_config(runtime_config, env=env)

    # Merge any caller-supplied clients; normalise keys to lowercase so lookup
    # is case-insensitive and consistent with how auto-built keys are stored.
    if extra_clients:
        for provider, client in extra_clients.items():
            clients[str(provider).strip().lower()] = client

    if not clients:
        raise ValueError(
            "No provider clients could be created from runtime config. "
            "Add API keys or pass extra_clients."
        )

    # Resolve the default provider name from the runtime config so the adapter
    # knows which client to use when no explicit provider override is given.
    default_llm_key = runtime_config.get("default_llm")
    supported_llms = runtime_config.get("supported_llms", {})
    default_provider = None
    if default_llm_key and isinstance(supported_llms, dict):
        cfg = supported_llms.get(default_llm_key, {})
        if isinstance(cfg, dict):
            # ``provider`` is preferred; ``llm`` is accepted as a legacy alias.
            default_provider = cfg.get("provider") or cfg.get("llm")

    return ProviderDispatchLLMAdapter(clients=clients, default_provider=default_provider)

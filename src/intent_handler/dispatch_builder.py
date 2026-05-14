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
        runtime_config: Runtime configuration dict. Expected keys:

                        * ``nucore_runtime`` — mapping of profile name → config dict.
                          The ``default`` profile is used to resolve the adapter's default
                          provider when present.

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

    default_provider = None
    runtime_profiles = runtime_config.get("nucore_runtime")
    if isinstance(runtime_profiles, dict):
        cfg = runtime_profiles.get("default", {})
        if isinstance(cfg, dict):
            default_provider = cfg.get("provider")

    return ProviderDispatchLLMAdapter(clients=clients, default_provider=default_provider)

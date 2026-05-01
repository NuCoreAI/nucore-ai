from __future__ import annotations

from typing import Any
from .adapters import LLMAdapter, ToolSpec, ToolCall


class ProviderDispatchLLMAdapter(LLMAdapter):
    """LLM adapter that routes each call to the correct provider client at runtime.

    Rather than being tied to a single LLM backend, this adapter holds a
    registry of provider-specific :class:`~adapters.LLMAdapter` instances and
    selects the right one per-call based on the ``provider`` (or ``llm``) key
    present in the ``config`` dict passed to :meth:`generate`.

    This enables per-intent model routing: different intents can declare
    different providers in their ``llm_config`` without the runtime needing to
    know which concrete adapter class to use up front.

    Provider names are normalised to lowercase canonical forms before lookup
    (e.g. ``"anthropic"`` → ``"claude"``, ``"google"`` → ``"gemini"``).
    See :meth:`_normalize` for the full alias map.
    """

    provider_name = "dispatch"

    def __init__(
        self,
        clients: dict[str, LLMAdapter],
        *,
        default_provider: str | None = None,
    ) -> None:
        """Initialise the dispatch adapter with a set of provider clients.

        Args:
            clients:          Mapping of provider name → adapter instance.
                              Keys are normalised via :meth:`_normalize`, so
                              aliases like ``"anthropic"`` and ``"claude"``
                              both resolve to the same slot.
            default_provider: Name of the provider to use when a ``generate``
                              call does not specify one.  Defaults to the first
                              key in ``clients`` when ``None``.

        Raises:
            ValueError: If ``clients`` is empty, or if ``default_provider`` is
                        given but is not present in the registered clients.
        """
        if not clients:
            raise ValueError("ProviderDispatchLLMAdapter requires at least one provider client")

        # Normalise all incoming keys so lookups are alias-insensitive.
        normalized_clients: dict[str, LLMAdapter] = {}
        for provider, client in clients.items():
            normalized_clients[self._normalize(provider)] = client

        self._clients = normalized_clients

        if default_provider is None:
            # Fall back to whichever provider was registered first.
            self._default_provider = next(iter(normalized_clients.keys()))
        else:
            resolved_default = self._normalize(default_provider)
            if resolved_default not in normalized_clients:
                raise ValueError(
                    f"Default provider '{default_provider}' is not registered in clients"
                )
            self._default_provider = resolved_default

    # ------------------------------------------------------------------
    # Provider resolution
    # ------------------------------------------------------------------

    def get_adapter_for_provider(self, config: dict[str, Any] | None = None) -> tuple[LLMAdapter, dict[str, Any]]:
        """Resolve the adapter and effective config for a given call config.

        Reads ``config["provider"]`` (or ``config["llm"]`` as a legacy alias)
        to determine which registered client to use.  Falls back to
        ``self._default_provider`` when neither key is present.

        Args:
            config: Per-call LLM config dict.  May be ``None`` or empty.

        Returns:
            A ``(adapter, effective_config)`` tuple where ``effective_config``
            is a copy of ``config`` (or an empty dict).

        Raises:
            ValueError: If the resolved provider name has no registered client.
        """
        effective_config = dict(config or {})
        # Accept "provider" or legacy "llm" key; fall back to the registered default.
        provider = effective_config.get("provider") or effective_config.get("llm") or self._default_provider
        normalized_provider = self._normalize(str(provider))

        client = self._clients.get(normalized_provider)
        if client is None:
            available = sorted(self._clients.keys())
            raise ValueError(
                f"No LLM client registered for provider '{provider}'. Available providers: {available}"
            )
        return client, effective_config

    # ------------------------------------------------------------------
    # LLMAdapter interface
    # ------------------------------------------------------------------

    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
        """Route a generation request to the appropriate provider client.

        The target provider is resolved from ``config["provider"]`` (or
        ``config["llm"]``); the full ``config`` dict is forwarded as-is so
        model name, temperature, and other provider-specific settings are
        preserved.

        Args:
            messages:    Conversation history in the canonical
                         ``[{"role": ..., "content": ...}]`` format.
            config:      Per-call LLM config dict.  Must contain at least
                         ``provider`` or ``llm`` unless a default provider
                         was set at construction time.
            tools:       Optional list of exported tool dicts to pass to the
                         underlying adapter.
            expect_json: When ``True``, instruct the provider to return a
                         JSON-parseable response.

        Returns:
            The raw response dict from the underlying provider adapter.
        """
        client, effective_config = self.get_adapter_for_provider(config)
        if not client:
            return None
        return await client.generate(
            messages=messages,
            config=effective_config,
            tools=tools,
            expect_json=expect_json,
        )

    def register_provider(self, provider: str, client: LLMAdapter) -> None:
        """Register or replace a provider client at runtime.

        The provider name is normalised before storage so aliases are handled
        consistently with construction-time registration.

        Args:
            provider: Provider name (aliases accepted, e.g. ``"anthropic"``).
            client:   Adapter instance to associate with the provider.
        """
        self._clients[self._normalize(provider)] = client

    def export_tools(self, specs: list[ToolSpec]) -> Any:
        """Delegate :meth:`~adapters.LLMAdapter.export_tools` to the default provider client."""
        client, _ = self.get_adapter_for_provider()
        if not client:
            return None
        return client.export_tools(specs)

    def parse_tool_calls(self, response: Any) -> list[ToolCall]:
        """Delegate :meth:`~adapters.LLMAdapter.parse_tool_calls` to the default provider client."""
        client, _ = self.get_adapter_for_provider()
        if not client:
            return []
        return client.parse_tool_calls(response)

    def to_canonical_tools(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        """Delegate :meth:`~adapters.LLMAdapter.to_canonical_tools` to the default provider client."""
        client, _ = self.get_adapter_for_provider()
        if not client:
            return []
        return client.to_canonical_tools(tool_calls)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize(provider: str) -> str:
        """Return the canonical lowercase provider key for ``provider``.

        Alias map:

        +--------------------------+-------------+
        | Accepted aliases         | Canonical   |
        +==========================+=============+
        | ``anthropic``            | ``claude``  |
        +--------------------------+-------------+
        | ``gpt``                  | ``openai``  |
        +--------------------------+-------------+
        | ``xai``, ``x.ai``        | ``grok``    |
        +--------------------------+-------------+
        | ``google``               | ``gemini``  |
        +--------------------------+-------------+
        | ``llamacpp``,            | ``llama.cpp``|
        | ``llama_cpp``            |             |
        +--------------------------+-------------+

        Any value not in the alias map is returned lowercased and stripped.
        """
        p = (provider or "").strip().lower()
        if p in {"anthropic"}:
            return "claude"
        if p in {"gpt"}:
            return "openai"
        if p in {"xai", "x.ai"}:
            return "grok"
        if p in {"google"}:
            return "gemini"
        if p in {"llamacpp", "llama_cpp"}:
            return "llama.cpp"
        return p
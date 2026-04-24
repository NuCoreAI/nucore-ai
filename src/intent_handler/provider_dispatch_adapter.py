from __future__ import annotations

from typing import Any
from .adapters import LLMAdapter, ToolSpec, ToolCall



class ProviderDispatchLLMAdapter(LLMAdapter):
    """
    Dispatches each LLM call to the provider-specific client using runtime
    config fields (provider/llm). This enforces per-intent model routing.
    """

    provider_name = "dispatch"

    def __init__(
        self,
        clients: dict[str, LLMAdapter],
        *,
        default_provider: str | None = None,
    ) -> None:
        if not clients:
            raise ValueError("ProviderDispatchLLMAdapter requires at least one provider client")

        normalized_clients: dict[str, LLMAdapter] = {}
        for provider, client in clients.items():
            normalized_clients[self._normalize(provider)] = client

        self._clients = normalized_clients
        if default_provider is None:
            self._default_provider = next(iter(normalized_clients.keys()))
        else:
            resolved_default = self._normalize(default_provider)
            if resolved_default not in normalized_clients:
                raise ValueError(
                    f"Default provider '{default_provider}' is not registered in clients"
                )
            self._default_provider = resolved_default

    def get_adapter_for_provider(self, config: dict[str,Any]=None) -> LLMAdapter:
        effective_config = dict(config or {})
        provider = effective_config.get("provider") or effective_config.get("llm") or self._default_provider
        normalized_provider = self._normalize(str(provider))

        client = self._clients.get(normalized_provider)
        if client is None:
            available = sorted(self._clients.keys())
            raise ValueError(
                f"No LLM client registered for provider '{provider}'. Available providers: {available}"
            )
        return client, effective_config

    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
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
        self._clients[self._normalize(provider)] = client
    
    def export_tools(self, specs: list[ToolSpec]) -> Any:
        client, _ = self.get_adapter_for_provider()
        if not client:
            return None
        return client.export_tools(specs)
    
    def parse_tool_calls(self, response: Any) -> list[ToolCall]:
        client, _ = self.get_adapter_for_provider()
        if not client:
            return []
        return client.parse_tool_calls(response)

    def to_canonical_tools(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        client, _ = self.get_adapter_for_provider()
        if not client:
            return []
        return client.to_canonical_tools(tool_calls)

    @staticmethod
    def _normalize(provider: str) -> str:
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
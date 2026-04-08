from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models import ProviderCapabilities, ToolCall, ToolResult, ToolSpec


class BaseToolsAdapter(ABC):
    provider_name: str = "unknown"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities()

    def build_conversation(self, base_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return list(base_messages)

    def export_tools(self, specs: list[ToolSpec]) -> Any:
        return specs

    def request_payload(
        self,
        *,
        conversation: list[dict[str, Any]],
        tool_specs: list[ToolSpec],
        config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        payload = {
            "messages": conversation,
            "tools": self.export_tools(tool_specs),
            "config": config or {},
        }
        return payload

    @abstractmethod
    def parse_tool_calls(self, response: Any) -> list[ToolCall]:
        raise NotImplementedError

    @abstractmethod
    def extract_final_text(self, response: Any) -> str:
        raise NotImplementedError

    def append_model_response(self, conversation: list[dict[str, Any]], response: Any) -> None:
        text = self.extract_final_text(response)
        if text:
            conversation.append({"role": "assistant", "content": text})

    def append_tool_results(
        self,
        conversation: list[dict[str, Any]],
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> None:
        for call, result in zip(tool_calls, tool_results):
            conversation.append(
                {
                    "role": "tool",
                    "tool_call_id": call.call_id,
                    "name": call.name,
                    "content": result.result,
                    "is_error": result.is_error,
                }
            )
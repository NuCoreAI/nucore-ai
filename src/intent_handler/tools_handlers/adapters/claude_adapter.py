from __future__ import annotations

from typing import Any

from ..models import ProviderCapabilities, ToolCall, ToolResult, ToolSpec
from .base import BaseToolsAdapter


class ClaudeToolsAdapter(BaseToolsAdapter):
    provider_name = "claude"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_parallel_calls=True,
            supports_streaming_tool_args=True,
            requires_tool_result_id=True,
            supports_strict_json_schema=True,
            tool_choice_mode="auto",
        )

    def export_tools(self, specs: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.json_schema,
            }
            for spec in specs
        ]

    def parse_tool_calls(self, response: Any) -> list[ToolCall]:
        calls: list[ToolCall] = []
        if not isinstance(response, dict):
            return calls

        for block in response.get("content", []) or []:
            if block.get("type") != "tool_use":
                continue
            calls.append(
                ToolCall(
                    call_id=str(block.get("id", "")),
                    name=str(block.get("name", "")),
                    args=block.get("input", {}) if isinstance(block.get("input"), dict) else {},
                    provider=self.provider_name,
                    raw=block,
                )
            )
        return calls

    def extract_final_text(self, response: Any) -> str:
        if isinstance(response, str):
            return response
        if not isinstance(response, dict):
            return ""

        parts: list[str] = []
        for block in response.get("content", []) or []:
            if block.get("type") == "text" and block.get("text"):
                parts.append(str(block["text"]))
        return "\n".join(parts)

    def append_tool_results(
        self,
        conversation: list[dict[str, Any]],
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> None:
        tool_blocks = []
        for call, result in zip(tool_calls, tool_results):
            tool_blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": call.call_id,
                    "content": result.result,
                    "is_error": result.is_error,
                }
            )
        conversation.append({"role": "user", "content": tool_blocks})
from __future__ import annotations

from typing import Any

from ..models import ProviderCapabilities, ToolCall, ToolResult, ToolSpec
from .base import BaseToolsAdapter


class GeminiToolsAdapter(BaseToolsAdapter):
    provider_name = "gemini"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_parallel_calls=True,
            supports_streaming_tool_args=False,
            requires_tool_result_id=False,
            supports_strict_json_schema=True,
            tool_choice_mode="auto",
        )

    def export_tools(self, specs: list[ToolSpec]) -> list[dict[str, Any]]:
        function_declarations = []
        for spec in specs:
            function_declarations.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.json_schema,
                }
            )
        return [{"functionDeclarations": function_declarations}]

    def parse_tool_calls(self, response: Any) -> list[ToolCall]:
        calls: list[ToolCall] = []
        if not isinstance(response, dict):
            return calls

        candidates = response.get("candidates", []) or []
        for candidate in candidates:
            content = candidate.get("content", {})
            for part in content.get("parts", []) or []:
                function_call = part.get("functionCall") or part.get("function_call")
                if not function_call:
                    continue
                calls.append(
                    ToolCall(
                        call_id=str(function_call.get("id", function_call.get("name", ""))),
                        name=str(function_call.get("name", "")),
                        args=function_call.get("args", {}) if isinstance(function_call.get("args"), dict) else {},
                        provider=self.provider_name,
                        raw=function_call,
                    )
                )
        return calls

    def extract_final_text(self, response: Any) -> str:
        if isinstance(response, str):
            return response
        if not isinstance(response, dict):
            return ""

        text_parts: list[str] = []
        for candidate in response.get("candidates", []) or []:
            content = candidate.get("content", {})
            for part in content.get("parts", []) or []:
                if isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
        return "\n".join(text_parts)

    def append_tool_results(
        self,
        conversation: list[dict[str, Any]],
        *,
        tool_calls: list[ToolCall],
        tool_results: list[ToolResult],
    ) -> None:
        parts = []
        for call, result in zip(tool_calls, tool_results):
            parts.append(
                {
                    "functionResponse": {
                        "name": call.name,
                        "response": {
                            "name": call.name,
                            "content": result.result,
                            "is_error": result.is_error,
                        },
                    }
                }
            )
        conversation.append({"role": "user", "parts": parts})
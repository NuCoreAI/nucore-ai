from __future__ import annotations

import json
from typing import Any

from ..models import ProviderCapabilities, ToolCall, ToolSpec
from .base import BaseToolsAdapter


class OpenAIToolsAdapter(BaseToolsAdapter):
    provider_name = "openai"

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            supports_parallel_calls=True,
            supports_streaming_tool_args=True,
            requires_tool_result_id=True,
            supports_strict_json_schema=True,
            tool_choice_mode="auto",
        )

    def export_tools(self, specs: list[ToolSpec]) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for spec in specs:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": spec.json_schema,
                        "strict": spec.strict,
                    },
                }
            )
        return tools

    def parse_tool_calls(self, response: Any) -> list[ToolCall]:
        calls: list[ToolCall] = []
        if not isinstance(response, dict):
            return calls

        for call in response.get("tool_calls", []) or []:
            fn = call.get("function", {})
            raw_args = fn.get("arguments", {})
            args = self._coerce_json(raw_args)
            calls.append(
                ToolCall(
                    call_id=str(call.get("id", "")),
                    name=str(fn.get("name", "")),
                    args=args,
                    provider=self.provider_name,
                    raw=call,
                )
            )

        output_items = response.get("output", []) or []
        for item in output_items:
            if item.get("type") not in {"function_call", "tool_call"}:
                continue
            raw_args = item.get("arguments", {})
            args = self._coerce_json(raw_args)
            calls.append(
                ToolCall(
                    call_id=str(item.get("call_id", item.get("id", ""))),
                    name=str(item.get("name", "")),
                    args=args,
                    provider=self.provider_name,
                    raw=item,
                )
            )
        return calls

    def extract_final_text(self, response: Any) -> str:
        if isinstance(response, dict):
            if isinstance(response.get("text"), str):
                return response["text"]
            if isinstance(response.get("content"), str):
                return response["content"]
            if isinstance(response.get("output_text"), str):
                return response["output_text"]

            text_parts: list[str] = []
            for item in response.get("output", []) or []:
                if item.get("type") == "message":
                    for chunk in item.get("content", []) or []:
                        if chunk.get("type") in {"output_text", "text"} and chunk.get("text"):
                            text_parts.append(chunk["text"])
            if text_parts:
                return "\n".join(text_parts)
        if isinstance(response, str):
            return response
        return ""

    @staticmethod
    def _coerce_json(raw_args: Any) -> dict[str, Any]:
        if isinstance(raw_args, dict):
            return raw_args
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                return {"raw": raw_args}
        return {}
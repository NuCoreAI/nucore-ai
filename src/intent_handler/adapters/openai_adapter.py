from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from openai import AsyncOpenAI
from .base_adapter import LLMAdapter, ToolCall, ToolSpec 

class OpenAIAdapter(LLMAdapter):
    provider_name = "openai"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)

    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
        cfg = dict(config or {})
        model = cfg.get("model") or "gpt-4.1-mini"
        normalized_messages = self._normalize_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": normalized_messages,
        }
        if tools:
            kwargs["tools"] = tools
#        if "temperature" in cfg:
#            kwargs["temperature"] = cfg["temperature"]
#        if "max_tokens" in cfg:
#            kwargs["max_tokens"] = cfg["max_tokens"]
        if expect_json:
            kwargs["response_format"] = {"type": "json_object"}

        stream = bool(cfg.get("stream", False))
        stream_handler = cfg.get("stream_handler")
        if stream and callable(stream_handler):
            content_parts: list[str] = []
            tool_call_accumulator: dict[int, dict[str, Any]] = {}

            stream_response = await self._client.chat.completions.create(stream=True, **kwargs)
            async for chunk in stream_response:
                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue

                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue

                delta_content = getattr(delta, "content", None)
                if isinstance(delta_content, str) and delta_content:
                    content_parts.append(delta_content)
                    stream_handler(delta_content)

                delta_tool_calls = getattr(delta, "tool_calls", None) or []
                for tc in delta_tool_calls:
                    idx = int(getattr(tc, "index", 0) or 0)
                    bucket = tool_call_accumulator.setdefault(
                        idx,
                        {
                            "id": "",
                            "type": "function",
                            "function": {"name": "", "arguments": ""},
                        },
                    )

                    tc_id = getattr(tc, "id", None)
                    if isinstance(tc_id, str) and tc_id:
                        bucket["id"] = tc_id

                    tc_type = getattr(tc, "type", None)
                    if isinstance(tc_type, str) and tc_type:
                        bucket["type"] = tc_type

                    fn = getattr(tc, "function", None)
                    if fn is not None:
                        fn_name = getattr(fn, "name", None)
                        if isinstance(fn_name, str) and fn_name:
                            bucket["function"]["name"] = fn_name
                        fn_args = getattr(fn, "arguments", None)
                        if isinstance(fn_args, str) and fn_args:
                            bucket["function"]["arguments"] += fn_args

            raw_tool_calls = [tool_call_accumulator[i] for i in sorted(tool_call_accumulator)]
            raw_response = {"content": ".".join(content_parts), "tool_calls": raw_tool_calls}
            tool_calls = self.to_canonical_tools(self.parse_tool_calls(raw_response))
            return {
                "content": "".join(content_parts),
                "tool_calls": tool_calls,
                "raw": {
                    "streamed": True,
                    "chunks": len(content_parts),
                },
            }

        response = await self._client.chat.completions.create(**kwargs)
        message = response.choices[0].message
        raw_tool_calls = [
            {
                "id": call.id,
                "type": call.type,
                "function": {
                    "name": call.function.name,
                    "arguments": call.function.arguments,
                },
            }
            for call in (message.tool_calls or [])
        ]
        raw_response = {"content": message.content or "", "tool_calls": raw_tool_calls}
        tool_calls = self.to_canonical_tools(self.parse_tool_calls(raw_response))

        return {
            "content": message.content or "",
            "tool_calls": tool_calls,
            "raw": response.model_dump(),
        }

    def _normalize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for msg in messages or []:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "user")
            content = msg.get("content", "")
            if content is None:
                content_text = ""
            elif isinstance(content, str):
                content_text = content
            else:
                # Some OpenAI-compatible backends are strict and require content to be a plain string.
                try:
                    content_text = json.dumps(content, ensure_ascii=False)
                except Exception:
                    content_text = str(content)
            normalized.append({"role": role, "content": content_text})
        return normalized

    def export_tools(self, specs: list[ToolSpec]) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for spec in specs:
            parameters = self._normalize_openai_schema(spec.json_schema) if spec.strict else spec.json_schema
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": spec.name,
                        "description": spec.description,
                        "parameters": parameters,
                        "strict": spec.strict,
                    },
                }
            )
        return tools

    def _normalize_openai_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Normalize JSON Schema for OpenAI strict function-calling validation."""
        normalized = deepcopy(schema)
        self._normalize_schema_node(normalized)
        return normalized

    def _normalize_schema_node(self, node: Any) -> None:
        if not isinstance(node, dict):
            return

        node_type = node.get("type")
        type_list = node_type if isinstance(node_type, list) else [node_type]
        is_object = "object" in type_list or "properties" in node

        if is_object:
            node.setdefault("additionalProperties", False)
            properties = node.get("properties")
            if isinstance(properties, dict):
                for prop_schema in properties.values():
                    self._normalize_schema_node(prop_schema)

                current_required = node.get("required")
                if not isinstance(current_required, list):
                    current_required = []

                required_set = {k for k in current_required if isinstance(k, str)}
                for key, prop_schema in properties.items():
                    if key not in required_set:
                        self._allow_null(prop_schema)
                        current_required.append(key)
                        required_set.add(key)

                node["required"] = current_required

        items = node.get("items")
        if isinstance(items, dict):
            self._normalize_schema_node(items)
        elif isinstance(items, list):
            for item in items:
                self._normalize_schema_node(item)

        for keyword in ("anyOf", "oneOf", "allOf"):
            variants = node.get(keyword)
            if isinstance(variants, list):
                for variant in variants:
                    self._normalize_schema_node(variant)

    def _allow_null(self, schema: Any) -> None:
        if not isinstance(schema, dict):
            return

        schema_type = schema.get("type")
        if isinstance(schema_type, str):
            if schema_type != "null":
                schema["type"] = [schema_type, "null"]
        elif isinstance(schema_type, list):
            if "null" not in schema_type:
                schema_type.append("null")
        elif schema_type is None:
            schema["type"] = ["null"]
    
    def to_canonical_tools(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        """Convert ToolCall objects to canonical Claude tool_use format."""
        return [
            {"type": "tool_use", "id": tc.call_id, "name": tc.name, "input": tc.args}
            for tc in tool_calls
        ]


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

    def _coerce_json(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return {}
            try:
                parsed = json.loads(text)
                return parsed if isinstance(parsed, dict) else {}
            except Exception:
                return {}
        return {}
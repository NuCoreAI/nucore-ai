from __future__ import annotations

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
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools
        if "temperature" in cfg:
            kwargs["temperature"] = cfg["temperature"]
        if "max_tokens" in cfg:
            kwargs["max_tokens"] = cfg["max_tokens"]
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
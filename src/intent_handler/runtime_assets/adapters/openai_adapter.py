from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI


class OpenAIAdapter:
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

            tool_calls = [tool_call_accumulator[i] for i in sorted(tool_call_accumulator)]
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
        tool_calls = []
        for call in message.tool_calls or []:
            tool_calls.append(
                {
                    "id": call.id,
                    "type": call.type,
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
            )

        return {
            "content": message.content or "",
            "tool_calls": tool_calls,
            "raw": response.model_dump(),
        }

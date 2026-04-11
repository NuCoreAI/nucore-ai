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

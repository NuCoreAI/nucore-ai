from __future__ import annotations

from typing import Any

from anthropic import AsyncAnthropic


class ClaudeAdapter:
    provider_name = "claude"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        self._client = AsyncAnthropic(api_key=api_key, base_url=base_url)

    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
        cfg = dict(config or {})
        model = cfg.get("model") or "claude-sonnet-4-20250514"

        system_parts: list[str] = []
        anthropic_messages: list[dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "system":
                system_parts.append(content)
                continue
            anthropic_role = "assistant" if role == "assistant" else "user"
            anthropic_messages.append({"role": anthropic_role, "content": content})

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": int(cfg.get("max_tokens", 4096)),
        }
        if system_parts:
            kwargs["system"] = "\n\n".join(system_parts)
        if tools:
            kwargs["tools"] = tools
        if "temperature" in cfg:
            kwargs["temperature"] = cfg["temperature"]

        stream = bool(cfg.get("stream", False))
        stream_handler = cfg.get("stream_handler")
        if stream and callable(stream_handler):
            callback = stream_handler
            async with self._client.messages.stream(**kwargs) as response_stream:
                async for text_chunk in response_stream.text_stream:
                    callback(text_chunk)
                final_message = await response_stream.get_final_message()

            content = final_message.content
            text_parts = [block.text for block in content if getattr(block, "type", "") == "text"]
            return {
                "content": [block.model_dump() for block in content],
                "text": "\n".join(text_parts),
                "raw": final_message.model_dump(),
            }

        response = await self._client.messages.create(**kwargs)
        content = response.content
        text_parts = [block.text for block in content if getattr(block, "type", "") == "text"]

        return {
            "content": [block.model_dump() for block in content],
            "text": "\n".join(text_parts),
            "raw": response.model_dump(),
        }

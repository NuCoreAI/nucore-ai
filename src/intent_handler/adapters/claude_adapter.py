from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic
from .base_adapter import LLMAdapter, ToolCall, ToolSpec


class ClaudeAdapter(LLMAdapter):
    """LLM adapter for Anthropic Claude models.

    Uses the official ``anthropic`` Python SDK (async). Supports both
    streaming and non-streaming requests as well as tool/function calling
    via Claude's native ``tool_use`` content blocks.
    """

    provider_name = "claude"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        """Initialise the adapter.

        Args:
            api_key:  Anthropic API key. Falls back to the ``ANTHROPIC_API_KEY``
                      environment variable when omitted.
            base_url: Optional custom endpoint (useful for proxies or testing).
        """
        self._client = AsyncAnthropic(api_key=api_key, base_url=base_url)

    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
        """Send a message to Claude and return a normalised response dict.

        ``system`` role messages are collected and joined into Claude's
        top-level ``system`` parameter; all other roles are forwarded as-is.

        When ``config["stream"]`` is True and ``config["stream_handler"]`` is
        callable, tokens are forwarded to the handler in real time and the
        adapter waits for the final message before returning.

        Returns a dict with keys:
            - ``content``: list of content block dicts (or joined text string
              for the streaming path)
            - ``text``:  plain text extracted from text content blocks
            - ``tool_calls``: canonical tool_use dicts (may be empty)
            - ``raw``: original SDK response as a dict
        """
        cfg = dict(config or {})
        model = cfg.get("model") or "claude-sonnet-4-20250514"

        # Separate system messages from the turn-by-turn conversation.
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
            # Ephemeral cache control keeps system prompts out of token counts
            # on repeated calls with the same system content.
            "cache_control": {"type": "ephemeral"},
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
            # Streaming path: push text chunks to the handler as they arrive,
            # then collect the final message for tool-call extraction.
            callback = stream_handler
            async with self._client.messages.stream(**kwargs) as response_stream:
                async for text_chunk in response_stream.text_stream:
                    callback(text_chunk)
                final_message = await response_stream.get_final_message()

            content = final_message.content
            text_parts = [block.text for block in content if getattr(block, "type", "") == "text"]
            raw_response = {"content": [block.model_dump() for block in content]}
            tool_calls = self.to_canonical_tools(self.parse_tool_calls(raw_response))
            return {
                "content": raw_response["content"],
                "text": "\n".join(text_parts),
                "tool_calls": tool_calls,
                "raw": final_message.model_dump(),
            }

        # Non-streaming path: single round-trip, parse response immediately.
        response = await self._client.messages.create(**kwargs)
        content = response.content
        text_parts = [block.text for block in content if getattr(block, "type", "") == "text"]
        content_dicts = [block.model_dump() for block in content]
        raw_response = {"content": content_dicts}
        tool_calls = self.to_canonical_tools(self.parse_tool_calls(raw_response))

        return {
            "content": content_dicts,
            "text": "\n".join(text_parts),
            "tool_calls": tool_calls,
            "raw": response.model_dump(),
        }

    def export_tools(self, specs: list[ToolSpec]) -> list[dict[str, Any]]:
        """Convert :class:`ToolSpec` objects to Claude's native tool format.

        Claude expects tools as a list of dicts with ``name``, ``description``,
        and ``input_schema`` (JSON Schema).
        """
        return [
            {
                "name": spec.name,
                "description": spec.description,
                "input_schema": spec.json_schema,
            }
            for spec in specs
        ]

    def parse_tool_calls(self, response: Any) -> list[ToolCall]:
        """Extract ``tool_use`` content blocks from a Claude response dict.

        Args:
            response: The normalised response dict returned by :meth:`generate`
                      (must contain a ``content`` list of block dicts).

        Returns:
            A list of :class:`ToolCall` instances, one per ``tool_use`` block.
        """
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
                    # ``input`` holds the argument dict; guard against non-dict values.
                    args=block.get("input", {}) if isinstance(block.get("input"), dict) else {},
                    provider=self.provider_name,
                    raw=block,
                )
            )
        return calls

    def to_canonical_tools(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        """Serialise :class:`ToolCall` objects back to Claude ``tool_use`` dicts.

        This canonical format is shared across all adapters so that handlers
        do not need to be aware of provider-specific wire formats.
        """
        return [
            {"type": "tool_use", "id": tc.call_id, "name": tc.name, "input": tc.args}
            for tc in tool_calls
        ]


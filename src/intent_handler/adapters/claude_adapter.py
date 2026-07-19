from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic
from .base_adapter import LLMAdapter, ToolCall, ToolSpec, stringify_tool_result


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

        Always issues the request via the SDK's streaming transport
        (``self._client.messages.stream``), regardless of ``config["stream"]``
        -- the Anthropic SDK itself refuses non-streaming calls whose
        ``max_tokens`` implies the response could take longer than 10
        minutes (``ValueError: Streaming is required for operations that may
        take longer than 10 minutes``), so a caller that configures a large
        ``max_tokens`` without a live UI to stream to still needs the
        streaming transport under the hood. Whether chunks are actually
        forwarded anywhere is a separate, independent decision: when
        ``config["stream_handler"]`` is callable, each text chunk is
        forwarded to it as it arrives; when it isn't, chunks are collected
        silently and only the final assembled response is returned -- so a
        caller with no live handler still gets exactly one result, not a
        live stream *and* a duplicate final print.

        Returns a dict with keys:
            - ``content``: list of content block dicts
            - ``text``:  plain text extracted from text content blocks
            - ``tool_calls``: canonical tool_use dicts (may be empty)
            - ``raw``: original SDK response as a dict
        """
        cfg = dict(config or {})
        model = cfg.get("model") or "claude-sonnet-5"

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
        }
        if system_parts:
            # Mark the system block itself as the cache breakpoint. The
            # top-level `cache_control` kwarg instead marks the *last*
            # cacheable block in the request -- which is the current (always
            # different) user turn, not this static system prompt -- so a
            # fresh single-turn call never got a cache hit on the system
            # prompt no matter how many times the same device/routine
            # database was sent before.
            kwargs["system"] = [
                {
                    "type": "text",
                    "text": "\n\n".join(system_parts),
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        if tools:
            kwargs["tools"] = tools
        if "temperature" in cfg:
            kwargs["temperature"] = cfg["temperature"]

        stream_handler = cfg.get("stream_handler")
        callback = stream_handler if callable(stream_handler) else None

        # Always use the streaming transport (see docstring) -- only forward
        # chunks to a callback when one is actually configured.
        async with self._client.messages.stream(**kwargs) as response_stream:
            if callback is not None:
                async for text_chunk in response_stream.text_stream:
                    await callback(text_chunk)
            final_message = await response_stream.get_final_message()

        content = final_message.content
        text_parts = [block.text for block in content if getattr(block, "type", "") == "text"]
        content_dicts = [block.model_dump() for block in content]
        raw_response = {"content": content_dicts}
        tool_calls = self.to_canonical_tools(self.parse_tool_calls(raw_response))
        if callback is not None:
            await callback("", is_end=True)  # Signal end of stream to the handler.

        return {
            "content": content_dicts,
            "text": "\n".join(text_parts),
            "tool_calls": tool_calls,
            "raw": final_message.model_dump(),
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

    @staticmethod
    def _sanitize_content_block(block: dict[str, Any]) -> dict[str, Any]:
        """Strip a response content block down to the fields the Messages API
        accepts as *input* when it's echoed back on the next turn.

        ``block.model_dump()`` (in :meth:`generate`) serializes the Anthropic
        SDK's response objects verbatim, which can carry response-only
        fields (e.g. a ``text`` block's ``parsed_output``, added by newer
        SDK/API versions) that the API rejects with "Extra inputs are not
        permitted" if fed straight back in as request content. Known block
        types are rebuilt from only their accepted fields; anything
        unrecognized is passed through as-is (best effort) rather than
        silently dropped.
        """
        block_type = block.get("type")
        if block_type == "text":
            return {"type": "text", "text": block.get("text", "")}
        if block_type == "tool_use":
            return {"type": "tool_use", "id": block.get("id"), "name": block.get("name"), "input": block.get("input", {})}
        return block

    def build_tool_round_trip_messages(
        self,
        *,
        raw_response: Any,
        tool_calls: list[ToolCall],
        tool_results: list[Any],
        config: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the assistant ``tool_use`` turn + user ``tool_result`` turn.

        :meth:`generate` already forwards arbitrary (string or
        content-block-list) ``content`` into ``anthropic_messages``
        unmodified -- but the content blocks themselves must be sanitized
        first (see :meth:`_sanitize_content_block`), since they originate
        from a *response* object, not hand-built request input.
        """
        content_blocks = [self._sanitize_content_block(b) for b in raw_response.get("content", [])]
        assistant_message = {"role": "assistant", "content": content_blocks}
        tool_result_blocks = [
            {
                "type": "tool_result",
                "tool_use_id": tc.call_id,
                "content": stringify_tool_result(result),
            }
            for tc, result in zip(tool_calls, tool_results)
        ]
        user_message = {"role": "user", "content": tool_result_blocks}
        return [assistant_message, user_message]


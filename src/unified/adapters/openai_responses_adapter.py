from __future__ import annotations

import json
from typing import Any

from .base_adapter import ToolCall, ToolSpec, stringify_tool_result
from .openai_adapter import OpenAIAdapter, _usage_dict


class OpenAIResponsesAdapter(OpenAIAdapter):
    """LLM adapter for OpenAI's ``/v1/responses`` endpoint.

    For reasoning-tier models that reject function tools under every
    ``reasoning_effort`` value on ``chat.completions`` (the exhausted retry
    cascade in :class:`OpenAIAdapter._create_chat_completion`) -- OpenAI's
    own error for these models points at this endpoint instead. Opted into
    per-profile via ``runtime_config.json``'s ``"provider": "openai-responses"``
    (see ``provider_clients.py``); every other OpenAI profile is unaffected.

    Subclasses :class:`OpenAIAdapter` to reuse the parts that are genuinely
    provider-shape-independent (strict-mode JSON Schema normalisation,
    stringified-argument coercion, client construction, the
    ``_extra_headers`` hook) -- everything else is overridden because the
    request/response shape genuinely differs: ``instructions`` instead of
    ``system`` messages, ``input`` items instead of ``messages``, a nested
    ``reasoning: {"effort": ...}`` instead of a flat ``reasoning_effort``,
    a flat tool-definition shape, and a typed streaming event union instead
    of ``ChatCompletionChunk``s.
    """

    provider_name = "openai_responses"

    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
        """Send a request to ``/v1/responses`` and return a normalised dict.

        Returns a dict with keys:
            - ``content``: assistant text reply (str, via ``response.output_text``)
            - ``tool_calls``: canonical tool_use dicts (may be empty)
            - ``usage``: token-usage dict (``input_tokens``/``output_tokens``, ...)
            - ``raw``: original SDK response, or a streaming summary dict

        No retry cascade here (unlike :class:`OpenAIAdapter`'s
        ``reasoning_effort``-vs-tools dance) -- exhausting that cascade on
        ``chat.completions`` is the entire reason a model needs this
        endpoint in the first place.
        """
        cfg = dict(config or {})
        model = cfg.get("model") or "gpt-4.1-mini"
        instructions, input_items = self._split_messages_for_responses(messages)
        kwargs: dict[str, Any] = {"model": model, "input": input_items}
        if instructions:
            kwargs["instructions"] = instructions
        if tools:
            kwargs["tools"] = tools
        if cfg.get("reasoning_effort"):
            kwargs["reasoning"] = {"effort": cfg["reasoning_effort"]}
        if expect_json:
            kwargs["text"] = {"format": {"type": "json_object"}}

        extra_headers = self._extra_headers(cfg)

        stream = bool(cfg.get("stream", False))
        stream_handler = cfg.get("stream_handler")
        if stream and callable(stream_handler):
            return await self._generate_streaming(kwargs, extra_headers, stream_handler)

        response = await self._client.responses.create(extra_headers=extra_headers, **kwargs)
        output_items = [item.model_dump() for item in (response.output or [])]
        raw_response = {"output": output_items}
        tool_calls = self.to_canonical_tools(self.parse_tool_calls(raw_response))

        return {
            "content": response.output_text or "",
            "tool_calls": tool_calls,
            # Every output item (not just function_call ones) -- e.g. a
            # web_search_call item never becomes a ToolCall (see
            # parse_tool_calls' function_call-only filter) and so never
            # reaches dispatch, but a caller's on_raw_response hook (see
            # AgenticLoop) still needs to see it to harvest evidence.
            "output": output_items,
            "usage": _usage_dict(getattr(response, "usage", None)),
            "raw": response.model_dump(),
        }

    async def _generate_streaming(
        self,
        kwargs: dict[str, Any],
        extra_headers: dict[str, str] | None,
        stream_handler: Any,
    ) -> dict[str, Any]:
        """Consume a streamed ``responses.create()`` call, forwarding text
        deltas to *stream_handler* and capturing each completed output item
        along the way (not just ``function_call`` ones -- see ``generate``'s
        ``"output"`` key for why).

        Unlike :class:`OpenAIAdapter`'s streaming path, no manual argument-
        fragment accumulation across chunks is needed: ``response.
        output_item.done`` hands back the fully assembled item in one event.
        """
        content_parts: list[str] = []
        output_items: list[dict[str, Any]] = []
        usage: dict[str, Any] = {}

        stream_response = await self._client.responses.create(stream=True, extra_headers=extra_headers, **kwargs)
        async for event in stream_response:
            event_type = getattr(event, "type", None)
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", None)
                if isinstance(delta, str) and delta:
                    content_parts.append(delta)
                    await stream_handler(delta)
            elif event_type == "response.output_item.done":
                item = getattr(event, "item", None)
                if item is not None:
                    output_items.append(item.model_dump())
            elif event_type == "response.completed":
                response = getattr(event, "response", None)
                usage = _usage_dict(getattr(response, "usage", None))

        raw_response = {"output": output_items}
        tool_calls = self.to_canonical_tools(self.parse_tool_calls(raw_response))
        return {
            "content": "".join(content_parts),
            "tool_calls": tool_calls,
            "output": output_items,
            "usage": usage,
            "raw": {"streamed": True, "chunks": len(content_parts)},
        }

    def _split_messages_for_responses(
        self, messages: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        """Split the canonical ``messages`` list into Responses'
        ``(instructions, input)`` pair.

        A message already in Responses-native shape (``"type"`` of
        ``"function_call"``/``"function_call_output"`` -- i.e. something
        this adapter's own :meth:`build_tool_round_trip_messages` appended
        in an earlier round of the same turn) passes through unchanged. A
        plain ``{"role", "content"}`` message with ``role == "system"`` is
        pulled into ``instructions`` instead (Responses' dedicated system-
        prompt channel, joined blank-line-separated the same way
        :meth:`OpenAIAdapter._normalize_messages` joins consecutive system
        messages); everything else becomes a plain input message item.
        """
        instructions_parts: list[str] = []
        input_items: list[dict[str, Any]] = []
        for msg in messages or []:
            if not isinstance(msg, dict):
                continue
            if msg.get("type") in {"function_call", "function_call_output"}:
                input_items.append(msg)
                continue

            role = str(msg.get("role") or "user")
            content = msg.get("content", "")
            if content is None:
                content_text = ""
            elif isinstance(content, str):
                content_text = content
            else:
                try:
                    content_text = json.dumps(content, ensure_ascii=False)
                except Exception:
                    content_text = str(content)

            if role == "system":
                if content_text:
                    instructions_parts.append(content_text)
                continue

            input_items.append(
                {
                    "type": "message",
                    "role": "assistant" if role == "assistant" else "user",
                    "content": content_text,
                }
            )
        return "\n\n".join(instructions_parts), input_items

    def export_tools(self, specs: list[ToolSpec]) -> list[dict[str, Any]]:
        """Convert :class:`ToolSpec` objects to Responses' flat tool shape
        (``{"type", "name", "description", "parameters", "strict"}``,
        unlike ``chat.completions``' nested ``{"type", "function": {...}}``).
        Strict-mode schema normalisation itself is inherited unchanged from
        :class:`OpenAIAdapter` -- identical rules on both endpoints."""
        tools: list[dict[str, Any]] = []
        for spec in specs:
            parameters = self._normalize_openai_schema(spec.json_schema) if spec.strict else spec.json_schema
            tools.append(
                {
                    "type": "function",
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": parameters,
                    "strict": spec.strict,
                }
            )
        return tools

    def build_tool_round_trip_messages(
        self,
        *,
        raw_response: Any,
        tool_calls: list[ToolCall],
        tool_results: list[Any],
        config: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Return the prior ``function_call`` item(s) followed by their
        ``function_call_output`` item(s) -- Responses has no assistant-turn
        echo requirement the way ``chat.completions`` does, it just wants
        both item types present as ``input`` on the next call. These round
        trip right back in via :meth:`_split_messages_for_responses`'s
        pass-through branch, which recognises them on the next round.
        """
        items: list[dict[str, Any]] = []
        for tc, result in zip(tool_calls, tool_results):
            items.append(
                {
                    "type": "function_call",
                    "call_id": tc.call_id,
                    "name": tc.name,
                    "arguments": json.dumps(tc.args, ensure_ascii=False),
                }
            )
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": tc.call_id,
                    "output": stringify_tool_result(result),
                }
            )
        return items

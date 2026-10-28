from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from openai import AsyncOpenAI, BadRequestError
from .base_adapter import LLMAdapter, ToolCall, ToolSpec, stringify_tool_result


def _usage_dict(usage: Any) -> dict[str, Any]:
    """Normalize an SDK usage object (or None, or a fake test double without
    one) to a plain dict -- real ChatCompletion.usage is a pydantic model
    with .model_dump(), but nothing here should crash generate() over a
    debug-only stat."""
    model_dump = getattr(usage, "model_dump", None)
    return model_dump() if callable(model_dump) else {}


class OpenAIAdapter(LLMAdapter):
    """LLM adapter for OpenAI chat completion models.

    Uses the official ``openai`` Python SDK (async). Handles streaming with
    incremental tool-call argument accumulation, strict JSON Schema
    normalisation required by OpenAI function-calling, and Responses API
    ``output`` items in addition to the standard ``tool_calls`` field.
    """

    provider_name = "openai"

    # gpt-5-mini (a reasoning model) rejects temperature/max_tokens, so the
    # default OpenAI path omits them. Subclasses for providers without that
    # restriction (e.g. llama.cpp) can opt back in.
    _forward_temperature_and_max_tokens = False
    # Some OpenAI-compatible servers (e.g. llama.cpp builds) reject the
    # response_format object; subclasses can disable it and rely on prompt
    # instructions for JSON output instead.
    _supports_response_format = True
    # stream_options={"include_usage": True} is what makes a streamed
    # response's final chunk carry `usage` at all -- real OpenAI supports it,
    # but an arbitrary self-hosted OpenAI-compatible server might reject an
    # unrecognized field, so subclasses for those can opt out.
    _supports_stream_usage = True

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        """Initialise the adapter.

        Args:
            api_key:  OpenAI API key. Falls back to ``OPENAI_API_KEY`` when omitted.
            base_url: Optional endpoint override (used by :class:`OpenAICompatibleAdapter`).
        """
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        # Models discovered (via a live 400) to reject function tools when
        # combined with the configured reasoning_effort on chat.completions
        # -- see _create_chat_completion()'s retry cascade. Keyed by model,
        # value is what to send instead when tools are present: "none" for a
        # model that accepts that value, or None to omit the field entirely
        # for a model that doesn't support "none" either (its reasoning_effort
        # only accepts e.g. low/medium/high/xhigh). Persists for this adapter
        # instance's lifetime so only the first call(s) to a given such model
        # ever pay for the failing round-trip(s); later calls apply the
        # remembered override up front.
        self._reasoning_effort_tools_override: dict[str, str | None] = {}

    def _extra_headers(self, cfg: dict[str, Any]) -> dict[str, str] | None:
        """Hook for a subclass to attach extra HTTP headers to the
        underlying ``chat.completions.create()`` call (e.g. Grok's
        cache-routing header). ``None`` (the default) means no extra
        headers -- plain OpenAI needs none."""
        return None

    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
        """Send a chat completion request to OpenAI.

        When ``config["stream"]`` is True and ``config["stream_handler"]`` is
        callable, text deltas are forwarded to the handler and tool-call
        argument fragments are accumulated across chunks into complete dicts.

        Returns a dict with keys:
            - ``content``: assistant text reply (str)
            - ``tool_calls``: canonical tool_use dicts (may be empty)
            - ``raw``:  original SDK response or a streaming summary dict
        """
        cfg = dict(config or {})
        model = cfg.get("model") or "gpt-4.1-mini"
        # OpenAI requires content to always be a plain string; normalise here.
        normalized_messages = self._normalize_messages(messages)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": normalized_messages,
        }
        if tools:
            kwargs["tools"] = tools
        if self._forward_temperature_and_max_tokens:
            if "temperature" in cfg:
                kwargs["temperature"] = cfg["temperature"]
            if "max_tokens" in cfg:
                kwargs["max_tokens"] = cfg["max_tokens"]
        if cfg.get("reasoning_effort"):
            # Opt-in only -- not every model accepts this field, but some
            # reasoning-tier models reject function tools on chat.completions
            # unless it's explicitly set (e.g. to "none"). The opposite quirk
            # also exists (see _create_chat_completion()'s retry cascade): a
            # model already caught rejecting tools + the configured effort
            # gets the remembered override applied up front, skipping the
            # failing round-trip(s).
            effort = cfg["reasoning_effort"]
            if tools and model in self._reasoning_effort_tools_override:
                effort = self._reasoning_effort_tools_override[model]
            if effort:
                kwargs["reasoning_effort"] = effort
        if expect_json and self._supports_response_format:
            # Ask the model to guarantee JSON output (no extra prose).
            kwargs["response_format"] = {"type": "json_object"}

        extra_headers = self._extra_headers(cfg)

        stream = bool(cfg.get("stream", False))
        stream_handler = cfg.get("stream_handler")
        if stream and callable(stream_handler):
            content_parts: list[str] = []
            # Map from call index → accumulated call dict so that fragmented
            # tool-call deltas from multiple chunks can be reassembled.
            tool_call_accumulator: dict[int, dict[str, Any]] = {}
            usage: dict[str, Any] = {}

            stream_kwargs = dict(kwargs)
            if self._supports_stream_usage:
                # Only the include_usage:True final chunk carries `usage` at
                # all -- without this, a streamed call never reports cache
                # hits, unlike the non-streaming path where usage always
                # rides along on the one response object.
                stream_kwargs["stream_options"] = {"include_usage": True}

            stream_response = await self._create_chat_completion(
                stream_kwargs, extra_headers, model=model, tools=tools, stream=True
            )
            async for chunk in stream_response:
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = _usage_dict(chunk_usage)

                choices = getattr(chunk, "choices", None) or []
                if not choices:
                    continue

                delta = getattr(choices[0], "delta", None)
                if delta is None:
                    continue

                # Accumulate text and forward each fragment to the caller.
                delta_content = getattr(delta, "content", None)
                if isinstance(delta_content, str) and delta_content:
                    content_parts.append(delta_content)
                    await stream_handler(delta_content)

                # Tool-call arguments arrive in fragments across chunks;
                # append argument strings and fill in id/name when present.
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

            # Re-order by index so tool calls appear in declaration order.
            raw_tool_calls = [tool_call_accumulator[i] for i in sorted(tool_call_accumulator)]
            raw_response = {"content": ".".join(content_parts), "tool_calls": raw_tool_calls}
            tool_calls = self.to_canonical_tools(self.parse_tool_calls(raw_response))
            return {
                "content": "".join(content_parts),
                "tool_calls": tool_calls,
                "native_tool_calls": raw_tool_calls,
                "usage": usage,
                "raw": {
                    "streamed": True,
                    "chunks": len(content_parts),
                },
            }

        # Non-streaming path: single request, parse tool calls from the message.
        response = await self._create_chat_completion(
            kwargs, extra_headers, model=model, tools=tools, stream=False
        )
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
            # OpenAI's own tool-call shape, preserved (not just the
            # provider-agnostic canonical form) so build_tool_round_trip_messages
            # can echo it back verbatim in the assistant turn -- OpenAI requires
            # the exact {id, type, function:{name, arguments}} shape it emitted.
            "native_tool_calls": raw_tool_calls,
            # prompt_tokens_details.cached_tokens is OpenAI's cache-hit count
            # (automatic caching, no cache_creation counter like Anthropic's --
            # a miss just reads 0, there's nothing else to report).
            "usage": _usage_dict(getattr(response, "usage", None)),
            "raw": response.model_dump(),
        }

    async def _create_chat_completion(
        self,
        kwargs: dict[str, Any],
        extra_headers: dict[str, str] | None,
        *,
        model: str,
        tools: list[dict[str, Any]] | None,
        stream: bool,
    ) -> Any:
        """Issue ``chat.completions.create()``, retrying with a
        progressively more conservative ``reasoning_effort`` when this model
        rejects the combination of tools + the configured value.

        Two tiers, matching the two live 400s seen in production for
        different reasoning-tier models: first downgrade to ``"none"``
        (``_is_reasoning_effort_tools_error``); if the model doesn't support
        ``"none"`` either (``_is_reasoning_effort_none_unsupported_error``),
        drop the field entirely and let the server use its own default.
        Mutates ``kwargs`` in place so the caller's copy reflects whatever
        ultimately succeeded, and remembers the working override on
        ``self._reasoning_effort_tools_override[model]`` so later calls to
        this model skip the failing round-trip(s) (see the preemptive check
        in :meth:`generate`).
        """
        while True:
            try:
                if stream:
                    return await self._client.chat.completions.create(
                        stream=True, extra_headers=extra_headers, **kwargs
                    )
                return await self._client.chat.completions.create(extra_headers=extra_headers, **kwargs)
            except BadRequestError as exc:
                if not tools:
                    raise
                effort = kwargs.get("reasoning_effort")
                if effort and effort != "none" and self._is_reasoning_effort_tools_error(exc):
                    kwargs["reasoning_effort"] = "none"
                    self._reasoning_effort_tools_override[model] = "none"
                    continue
                if effort == "none" and self._is_reasoning_effort_none_unsupported_error(exc):
                    del kwargs["reasoning_effort"]
                    self._reasoning_effort_tools_override[model] = None
                    continue
                raise

    def _normalize_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ensure every message's ``content`` is a plain string.

        OpenAI-compatible backends reject messages where ``content`` is a list
        or other non-string type.  Complex content is JSON-serialised as a
        fallback so no information is silently dropped.

        Tool-call round-trip messages (``role: "tool"``, or an assistant
        message carrying ``tool_calls``) are passed through unmodified
        instead: OpenAI requires their exact shape (``tool_call_id`` on a
        tool message, the native ``tool_calls`` list on an assistant
        message) to recognise a tool-result turn -- coercing them to a plain
        string would silently break the agentic tool-calling loop.

        Consecutive ``system`` messages are merged into one (joined with a
        blank line): the prompt arrives as several sections (a static part
        and a volatile tail, see prompt_builder), and some OpenAI-compatible
        chat templates (llama.cpp) reject more than one system message.
        """
        normalized: list[dict[str, Any]] = []
        for msg in messages or []:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role") or "user")
            if role == "tool" or (role == "assistant" and "tool_calls" in msg):
                normalized.append(msg)
                continue
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
            if role == "system" and normalized and normalized[-1].get("role") == "system":
                normalized[-1] = {"role": "system", "content": f"{normalized[-1]['content']}\n\n{content_text}"}
                continue
            normalized.append({"role": role, "content": content_text})
        return normalized

    def export_tools(self, specs: list[ToolSpec]) -> list[dict[str, Any]]:
        """Convert :class:`ToolSpec` objects to OpenAI function-calling format.

        When ``spec.strict`` is True the JSON Schema is normalised to satisfy
        OpenAI's strict mode requirements (all properties required, no
        additional properties, optional fields made nullable).
        """
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
        """Deep-copy ``schema`` and apply OpenAI strict-mode requirements.

        A fresh copy is made so the original :class:`ToolSpec` is not mutated.
        """
        normalized = deepcopy(schema)
        self._normalize_schema_node(normalized)
        return normalized

    def _normalize_schema_node(self, node: Any) -> None:
        """Recursively enforce OpenAI strict JSON Schema rules on a schema node.

        OpenAI strict mode requires:
        - Object nodes must set ``additionalProperties: false``.
        - Every property must appear in the ``required`` list.
        - Properties not originally required are made nullable (type becomes
          ``[original_type, "null"]``) so the model can still omit them.
        - Array ``items`` and combiner keywords (anyOf/oneOf/allOf) are
          normalised recursively.

        A node whose ``type`` includes ``"array"`` but omits ``items``
        entirely is valid, unconstrained plain JSON Schema, but OpenAI's
        strict mode rejects it -- and rejects a default ``items: {}`` just
        as hard (confirmed directly against a live 400: "schema must have a
        'type' key", because ``{}`` itself has no ``type``). There is no
        default that satisfies strict mode for a genuinely untyped item, so
        this is deliberately NOT patched here -- a tool whose array truly
        has no fixed item type needs ``"strict": false`` on the tool file
        itself instead (the same escape hatch ``tool_plugin_call.json`` uses
        for its free-form ``additionalProperties: true`` object), not a
        silent runtime rewrite. See ``tool_plugin_configure.json``.
        """
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
                        # Make the field nullable before adding to required so
                        # the model can signal "not provided" with null.
                        self._allow_null(prop_schema)
                        current_required.append(key)
                        required_set.add(key)

                node["required"] = current_required

        # Recurse into array item schemas.
        items = node.get("items")
        if isinstance(items, dict):
            self._normalize_schema_node(items)
        elif isinstance(items, list):
            for item in items:
                self._normalize_schema_node(item)

        # Recurse into combiner schemas.
        for keyword in ("anyOf", "oneOf", "allOf"):
            variants = node.get(keyword)
            if isinstance(variants, list):
                for variant in variants:
                    self._normalize_schema_node(variant)

    def _allow_null(self, schema: Any) -> None:
        """Expand a schema's ``type`` to include ``"null"`` if not already present.

        Handles three forms: plain string type, list of types, and missing type.
        """
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
        """Serialise :class:`ToolCall` objects to the shared canonical tool_use format.

        The canonical format mirrors Claude's ``tool_use`` content block so
        that intent handlers are provider-agnostic.
        """
        return [
            {"type": "tool_use", "id": tc.call_id, "name": tc.name, "input": tc.args}
            for tc in tool_calls
        ]

    def parse_tool_calls(self, response: Any) -> list[ToolCall]:
        """Extract tool calls from an OpenAI response dict.

        Handles two shapes:
        - Standard ``chat.completions`` response with a ``tool_calls`` list.
        - Responses API ``output`` list with ``function_call`` / ``tool_call``
          items (used by newer assistants endpoints).
        """
        calls: list[ToolCall] = []
        if not isinstance(response, dict):
            return calls

        # Standard chat completions tool_calls array.
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

        # Responses API output items.
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

    def build_tool_round_trip_messages(
        self,
        *,
        raw_response: Any,
        tool_calls: list[ToolCall],
        tool_results: list[Any],
        config: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the assistant tool-call turn + one ``role: "tool"`` message
        per result.

        Requires the exact native ``{id, type, function:{name, arguments}}``
        shape OpenAI itself emitted (``raw_response["native_tool_calls"]``,
        added to :meth:`generate`'s return dict specifically for this) --
        the provider-agnostic canonical ``tool_calls`` shape isn't enough,
        since OpenAI validates the echoed-back call shape strictly.
        """
        assistant_message: dict[str, Any] = {
            "role": "assistant",
            "content": raw_response.get("content") or None,
            "tool_calls": raw_response.get("native_tool_calls", []),
        }
        tool_messages = [
            {
                "role": "tool",
                "tool_call_id": tc.call_id,
                "content": stringify_tool_result(result),
            }
            for tc, result in zip(tool_calls, tool_results)
        ]
        return [assistant_message, *tool_messages]

    def _coerce_json(self, value: Any) -> dict[str, Any]:
        """Coerce a raw argument value to a dict.

        OpenAI returns tool arguments as a JSON string; this method parses
        that string and falls back gracefully on empty or malformed input.
        """
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

    @staticmethod
    def _is_reasoning_effort_tools_error(exc: BadRequestError) -> bool:
        """Detect the live 400 some reasoning-tier models return when both
        function tools and a non-"none" ``reasoning_effort`` are present on
        chat.completions (e.g. "Function tools with reasoning_effort are not
        supported for <model> ... set reasoning_effort to 'none'.").

        Keyed off the structured ``param``/``message`` fields rather than a
        bare "reasoning_effort" substring -- a sibling error (see
        ``_is_reasoning_effort_none_unsupported_error``) also mentions
        "reasoning_effort" and shares the same ``param``, so the distinct
        "function tool" wording is what actually separates the two.
        """
        # Unlike Anthropic's SDK (which leaves `body` as the full response
        # JSON), OpenAI's `_make_status_error` unwraps the "error" key itself
        # before constructing the exception -- `exc.body` here is already
        # the inner {"message": ..., "param": ..., ...} dict, not
        # {"error": {...}}. Confirmed directly against the installed SDK.
        body = getattr(exc, "body", None)
        if not isinstance(body, dict) or body.get("param") != "reasoning_effort":
            return False
        return "function tool" in str(body.get("message", "")).lower()

    @staticmethod
    def _is_reasoning_effort_none_unsupported_error(exc: BadRequestError) -> bool:
        """Detect "Unsupported value: 'reasoning_effort' does not support
        'none' with this model" -- some models accept reasoning_effort but
        never accept "none" (no way to disable reasoning at all), in which
        case the field must be omitted entirely rather than forced to
        "none". Keyed off the structured ``code`` field (OpenAI's own
        machine-readable signal for this exact case), not message text."""
        body = getattr(exc, "body", None)
        return (
            isinstance(body, dict)
            and body.get("param") == "reasoning_effort"
            and body.get("code") == "unsupported_value"
        )
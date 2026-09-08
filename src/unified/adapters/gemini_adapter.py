from __future__ import annotations

import json
import os
from typing import Any

import httpx
from utils import get_logger
from .base_adapter import LLMAdapter, ToolCall, ToolSpec, stringify_tool_result

logger = get_logger(__name__)


async def _raise_for_status_with_body(response: httpx.Response) -> None:
    """Like ``response.raise_for_status()``, but includes Google's actual JSON
    error body in the exception message.

    httpx's own message (e.g. "Client error '400 Bad Request' for url ...")
    never includes the response body, so a 400 caused by an unrecognised
    model name, a malformed key, or a rejected schema field all look
    identical from the caller's side. Google's body carries the real reason
    (e.g. ``"models/gemini-3.1-flash is not found for API version v1beta"``).
    ``.aread()`` works for both a fully-received (non-streaming) response and
    a streamed one whose body hasn't been read yet.
    """
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        body = await response.aread()
        raise httpx.HTTPStatusError(
            f"{exc}\nResponse body: {body.decode(errors='replace')}",
            request=exc.request,
            response=exc.response,
        ) from None


def _sanitize_schema_for_gemini(schema: Any) -> Any:
    """Recursively rewrite a Claude-authoring-format JSON Schema into
    something Gemini's function-declaration ``Schema`` proto can actually
    accept.

    Gemini validates tool schemas against a fixed proto and 400s the
    *entire* request over a single incompatibility anywhere in it (unlike
    most JSON Schema consumers, which just ignore keywords they don't
    recognise). Two incompatibilities show up throughout this repo's tool
    files, both from optional/nullable-parameter authoring patterns Gemini
    has no representation for:

    - ``additionalProperties`` -- no such field on the proto at all (used by
      a few tools' free-form ``params``/``args`` objects for OpenAI's
      strict-mode escape hatch).
    - ``"type": [<type>, "null"]`` -- the JSON-Schema nullable-union idiom
      used by nearly every optional parameter in these tool files. Gemini's
      ``type`` is a singular enum, not a repeated field, so a list value is
      rejected outright ("Proto field is not repeating, cannot start list").
      Rewritten to the single non-null type plus ``"nullable": true``; a
      genuine multi-type union (e.g. ``["number", "string", "object", "null"]``
      for `send_command`'s free-form ``value``) has no single-type
      equivalent, so ``type`` is dropped entirely (Gemini treats a schema
      node with no ``type`` as unconstrained) while ``nullable`` is still
      set when ``"null"`` was one of the options.
    - ``enum`` -- Gemini only supports it on a ``string``-typed schema node
      (with string values); an enum on any other resulting type is dropped
      since Gemini has no way to express it (values <1, 2> etc. stay
      documented in the field's ``description`` instead).
    - ``properties``/``required`` (object-only) and ``items`` (array-only) --
      valid only alongside a matching declared ``type``. A node whose
      ``type`` got dropped above (or that simply isn't object/array) but
      still carries one of these leftover keys is rejected ("...only
      allowed for OBJECT type"), so they're stripped whenever the resolved
      ``type`` doesn't match.
    """
    if isinstance(schema, list):
        return [_sanitize_schema_for_gemini(item) for item in schema]
    if not isinstance(schema, dict):
        return schema

    result = {
        key: _sanitize_schema_for_gemini(value)
        for key, value in schema.items()
        if key != "additionalProperties"
    }

    type_value = result.get("type")
    if isinstance(type_value, list):
        non_null_types = [t for t in type_value if t != "null"]
        if "null" in type_value:
            result["nullable"] = True
        if len(non_null_types) == 1:
            result["type"] = non_null_types[0]
        else:
            result.pop("type", None)

    if isinstance(result.get("enum"), list):
        if result.get("type") == "string":
            string_values = [v for v in result["enum"] if isinstance(v, str)]
            if string_values:
                result["enum"] = string_values
            else:
                result.pop("enum", None)
        else:
            result.pop("enum", None)

    # "properties"/"required" and "items" are only valid alongside their
    # matching declared type -- e.g. send_command's "value" carries a
    # "properties" hint for its object-shaped case, but once a genuine
    # multi-type union drops "type" above (or any other node isn't
    # "object"/"array"), Gemini rejects the leftover key outright ("...only
    # allowed for OBJECT type").
    resolved_type = result.get("type")
    if resolved_type != "object":
        result.pop("properties", None)
        result.pop("required", None)
    if resolved_type != "array":
        result.pop("items", None)

    return result


class GeminiAdapter(LLMAdapter):
    """LLM adapter for Google Gemini models via the REST ``generativelanguage`` API.

    Uses ``httpx`` directly (no official async SDK dependency) so the adapter
    stays lightweight.  Supports both streaming (SSE) and non-streaming
    requests.  When streaming fails (e.g. the endpoint does not expose
    ``streamGenerateContent``), the adapter transparently falls back to a
    standard ``generateContent`` call.
    """

    provider_name = "gemini"

    def __init__(self, *, api_key: str | None = None, base_url: str | None = None) -> None:
        """Initialise the adapter.

        Args:
            api_key:  Google AI Studio / Vertex AI API key.  Falls back to the
                      ``GEMINI_API_KEY`` environment variable when omitted.
            base_url: API root override (default:
                      ``https://generativelanguage.googleapis.com``).
        """
        self._api_key = api_key
        self._base_url = (base_url or "https://generativelanguage.googleapis.com").rstrip("/")

    async def generate(
        self,
        *,
        messages: list[dict[str, str]],
        config: dict[str, Any] | None = None,
        tools: list[dict[str, Any]] | None = None,
        expect_json: bool = False,
    ) -> Any:
        """Send a request to the Gemini ``generateContent`` endpoint.

        Gemini uses ``"model"`` for the assistant role (vs ``"assistant"`` used
        by other providers); this mapping is applied automatically.

        When streaming is requested the adapter calls
        :meth:`_stream_generate_content` and falls back silently on failure.

        Returns a dict with keys:
            - ``content``: joined plain text from all text parts
            - ``tool_calls``: canonical tool_use dicts (may be empty)
            - ``raw``:  full JSON response from the API
        """
        cfg = dict(config or {})
        model = cfg.get("model") or "gemini-flash-latest"
        # self._api_key first -- provider_clients.py already resolved any
        # "${VAR}" placeholder at adapter-construction time, but cfg (the raw
        # per-call runtime-config dict) still carries the unresolved literal,
        # which would otherwise win this precedence and get sent to Gemini
        # verbatim as a bogus key.
        api_key = self._api_key or cfg.get("api_key") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Gemini API key not provided. Set GEMINI_API_KEY or config.api_key")

        # System-role messages go in the dedicated systemInstruction field
        # (not the contents array) -- this is both the semantically correct
        # slot and the one Gemini's automatic/implicit context caching keys
        # off of. Everything else is translated to Gemini's "user" / "model"
        # role vocabulary.
        system_parts: list[str] = []
        contents = []
        for msg in messages:
            role = msg.get("role", "user")
            if role == "system":
                system_parts.append(msg.get("content", ""))
                continue
            # build_tool_round_trip_messages's own turns carry pre-built
            # native Gemini parts (functionCall/functionResponse) rather
            # than plain text -- everything else is a plain user/model text
            # turn. The functionResponse turn uses role "user", not the
            # dedicated "function" role the API also accepts on some model
            # versions: live-verified that "function" is REJECTED by
            # whichever model "gemini-flash-latest" currently resolves to
            # ("Role 'function' is not supported"), while "user" is accepted
            # by both that model and gemini-3.1-flash-lite -- since the
            # model is a runtime config value, only the role every version
            # accepts is safe to hardcode here.
            gemini_parts = msg.get("gemini_parts")
            if gemini_parts is not None:
                gemini_role = "model" if role == "assistant" else "user"
                contents.append({"role": gemini_role, "parts": gemini_parts})
                continue
            gemini_role = "model" if role == "assistant" else "user"
            contents.append({"role": gemini_role, "parts": [{"text": msg.get("content", "")}]})

        payload: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": cfg.get("temperature", 0.2),
                "maxOutputTokens": int(cfg.get("max_tokens", 4096)),
            },
        }
        if system_parts:
            payload["systemInstruction"] = {"parts": [{"text": "\n\n".join(system_parts)}]}
        if tools:
            payload["tools"] = tools
        if expect_json:
            # Instruct Gemini to return a pure JSON response body.
            payload["generationConfig"]["responseMimeType"] = "application/json"

        stream = bool(cfg.get("stream", False))
        stream_handler = cfg.get("stream_handler")
        if stream and callable(stream_handler):
            try:
                return await self._stream_generate_content(
                    model=model,
                    api_key=str(api_key),
                    payload=payload,
                    stream_handler=stream_handler,
                )
            except Exception as exc:
                # Fall back to non-streaming request for compatibility with endpoints
                # that don't expose streamGenerateContent. Logged (not silent) --
                # a real cause here (bad model name, rejected schema field, ...)
                # will fail generateContent too, and this is the only place that
                # would otherwise ever surface it.
                logger.warning(f"Gemini streamGenerateContent failed, falling back to generateContent: {exc}")

        url = f"{self._base_url}/v1beta/models/{model}:generateContent?key={api_key}"
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload)
            await _raise_for_status_with_body(response)
            data = response.json()

        text_parts: list[str] = []
        for candidate in data.get("candidates", []) or []:
            content = candidate.get("content", {})
            for part in content.get("parts", []) or []:
                if "text" in part:
                    text_parts.append(part["text"])

        parsed_calls = self.parse_tool_calls(data)

        return {
            "content": "".join(text_parts),
            "tool_calls": self.to_canonical_tools(parsed_calls),
            # The exact native parts (functionCall + thoughtSignature) Gemini
            # emitted, for build_tool_round_trip_messages to echo back
            # verbatim -- the canonical tool_calls shape above loses both.
            "native_tool_calls": [tc.raw for tc in parsed_calls],
            "raw": data,
        }

    async def _stream_generate_content(
        self,
        *,
        model: str,
        api_key: str,
        payload: dict[str, Any],
        stream_handler,
    ) -> Any:
        """Consume a Gemini SSE stream and forward text chunks to ``stream_handler``.

        The endpoint uses ``alt=sse`` to return newline-delimited ``data:`` lines,
        each carrying a JSON-encoded response fragment.  All chunks are retained
        so that tool calls present anywhere in the stream can be parsed after
        the stream closes.

        Returns the same shape as :meth:`generate` (non-streaming path), with
        ``raw.streamed`` set to ``True`` and the list of raw chunks stored in
        ``raw.chunks``.
        """
        url = f"{self._base_url}/v1beta/models/{model}:streamGenerateContent?alt=sse&key={api_key}"
        chunks: list[dict[str, Any]] = []
        text_parts: list[str] = []

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", url, json=payload) as response:
                await _raise_for_status_with_body(response)
                async for line in response.aiter_lines():
                    if not line:
                        continue
                    # SSE comment lines (keep-alive pings) start with ":".
                    if line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue

                    data_str = line[5:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue

                    chunk_json = json.loads(data_str)
                    chunks.append(chunk_json)

                    for candidate in chunk_json.get("candidates", []) or []:
                        content = candidate.get("content", {})
                        for part in content.get("parts", []) or []:
                            text = part.get("text")
                            if isinstance(text, str) and text:
                                text_parts.append(text)
                                await stream_handler(text)

        # Re-assemble a full response structure for the tools adapter to parse.
        # Each chunk is itself a full top-level response object (its own
        # "candidates" list nested inside) -- not a candidate -- so the list
        # of candidates across the whole stream has to be flattened out of
        # them, not just wrapped as-is (which silently produced zero tool
        # calls on every streamed function call: parse_tool_calls would call
        # chunk.get("content", {}) on each chunk wrapper, which has no
        # "content" key at that level, always finding nothing).
        all_candidates: list[dict[str, Any]] = []
        for chunk in chunks:
            all_candidates.extend(chunk.get("candidates", []) or [])
        combined_response = {"candidates": all_candidates}
        parsed_calls = self.parse_tool_calls(combined_response)

        return {
            "content": "".join(text_parts),
            "tool_calls": self.to_canonical_tools(parsed_calls),
            "native_tool_calls": [tc.raw for tc in parsed_calls],
            "raw": {
                "streamed": True,
                "chunks": chunks,
            },
        }

    def export_tools(self, specs: list[ToolSpec]) -> list[dict[str, Any]]:
        """Convert :class:`ToolSpec` objects to Gemini ``functionDeclarations`` format.

        Gemini expects a single ``tools`` entry whose ``functionDeclarations``
        key holds a list of function descriptors.
        """
        function_declarations = []
        for spec in specs:
            function_declarations.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": _sanitize_schema_for_gemini(spec.json_schema),
                }
            )
        return [{"functionDeclarations": function_declarations}]

    def parse_tool_calls(self, response: Any) -> list[ToolCall]:
        """Extract ``functionCall`` parts from a Gemini response (or stream chunk list).

        Gemini surfaces tool invocations as parts with either a ``functionCall``
        or ``function_call`` key (both spellings are checked for robustness).
        The ``id`` field is not always present; the function name is used as a
        fallback call identifier.

        ``raw`` is set to the *whole part* (not just the ``functionCall`` sub-dict)
        because Gemini 3's models attach a sibling ``thoughtSignature`` key to the
        same part -- live-verified: echoing a functionCall back on the next turn
        without its original ``thoughtSignature`` is rejected outright ("Function
        call is missing a thought_signature ... required for tools to work
        correctly"). :meth:`build_tool_round_trip_messages` needs the complete
        part, signature included, to echo back verbatim.
        """
        calls: list[ToolCall] = []
        if not isinstance(response, dict):
            return calls

        candidates = response.get("candidates", []) or []
        for candidate in candidates:
            content = candidate.get("content", {})
            for part in content.get("parts", []) or []:
                function_call = part.get("functionCall") or part.get("function_call")
                if not function_call:
                    continue
                calls.append(
                    ToolCall(
                        call_id=str(function_call.get("id", function_call.get("name", ""))),
                        name=str(function_call.get("name", "")),
                        args=function_call.get("args", {}) if isinstance(function_call.get("args"), dict) else {},
                        provider=self.provider_name,
                        raw=part,
                    )
                )
        return calls

    def to_canonical_tools(self, tool_calls: list[ToolCall]) -> list[dict[str, Any]]:
        """Serialise :class:`ToolCall` objects to the shared canonical tool_use format."""
        return [
            {"type": "tool_use", "id": tc.call_id, "name": tc.name, "input": tc.args}
            for tc in tool_calls
        ]

    def build_tool_round_trip_messages(
        self,
        *,
        raw_response: Any,
        tool_calls: list[ToolCall],
        tool_results: list[Any],
        config: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Build the model's functionCall turn + the functionResponse turn
        Gemini's multi-turn function-calling protocol requires.

        Requires the exact native Gemini parts Gemini itself emitted
        (``raw_response["native_tool_calls"]``, added to :meth:`generate`'s
        return dict specifically for this) -- by the time this runs,
        ``tool_calls[i].raw`` has already been overwritten by
        :class:`~unified.loop.AgenticLoop` with the provider-agnostic
        canonical tool_use dict. That substitution loses Gemini's own
        ``args`` key name and, critically, the sibling ``thoughtSignature``
        Gemini 3 attaches to each functionCall part -- live-verified:
        echoing a functionCall back without its original thoughtSignature
        is rejected outright ("Function call is missing a thought_signature
        ... required for tools to work correctly").

        The function-response turn is mapped to Gemini's ``"user"`` role by
        :meth:`generate`'s payload-building loop (see the comment there):
        the API also exposes a dedicated ``"function"`` role, but it's
        rejected outright on some model versions ("Role 'function' is not
        supported") while ``"user"`` is accepted everywhere tested, so this
        method itself stays role-agnostic and just tags the message
        ``"role": "tool"`` for that loop to translate. Gemini requires
        ``functionResponse.response`` to be a JSON object, so a non-dict
        tool result is wrapped as ``{"result": <value>}``.

        Both returned dicts also carry a ``"content"`` key -- ``gemini_parts``
        is the only thing :meth:`generate`'s payload-building loop actually
        reads, but every other adapter's round-trip messages always include
        ``"content"`` (Claude: content blocks; OpenAI: a real string or
        ``None``), so this keeps Gemini's from being a structural outlier for
        any other consumer (e.g. the debug prompt writer).
        """
        text = raw_response.get("content") if isinstance(raw_response, dict) else None
        native_parts = list(raw_response.get("native_tool_calls") or []) if isinstance(raw_response, dict) else []
        assistant_parts = ([{"text": text}] if text else []) + native_parts

        function_response_parts = []
        for tc, native_part, result in zip(tool_calls, native_parts, tool_results):
            native_call = native_part.get("functionCall") or native_part.get("function_call") or {}
            response_entry: dict[str, Any] = {
                "name": tc.name,
                "response": result if isinstance(result, dict) else {"result": result},
            }
            call_id = native_call.get("id")
            if call_id:
                response_entry["id"] = call_id
            function_response_parts.append({"functionResponse": response_entry})

        tool_content = "; ".join(stringify_tool_result(r) for r in tool_results) or None

        return [
            {"role": "assistant", "content": text or None, "gemini_parts": assistant_parts},
            {"role": "tool", "content": tool_content, "gemini_parts": function_response_parts},
        ]
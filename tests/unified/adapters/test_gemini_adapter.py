"""GeminiAdapter.generate -- api_key precedence between the constructor-time
key and the raw per-call config dict's own api_key field.

Regression coverage for a live bug: runtime_config.py never substitutes
"${VAR}"-style placeholders in a profile's api_key -- provider_clients.py is
the only place that does, once, at adapter-construction time, before handing
the real key to GeminiAdapter's constructor. generate() used to re-read
config["api_key"] and let that win over the already-resolved self._api_key,
so the still-literal "${GEMINI_API_KEY}" string from the runtime config JSON
got sent to Google as the actual key on every call.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import httpx

from unified.adapters.base_adapter import LLMAdapter, ToolCall
from unified.adapters import gemini_adapter as gemini_adapter_module
from unified.adapters.gemini_adapter import GeminiAdapter

_TOOLS_DIR = Path(__file__).parents[3] / "src" / "unified" / "tools"


class _FakeResponse:
    def __init__(self, data: dict[str, Any]):
        self._data = data

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self._data


class _FakeAsyncClient:
    captured_urls: list[str] = []
    captured_payloads: list[dict[str, Any]] = []
    next_response_data: dict[str, Any] = {"candidates": []}

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args) -> bool:
        return False

    async def post(self, url: str, json: Any = None) -> _FakeResponse:
        _FakeAsyncClient.captured_urls.append(url)
        _FakeAsyncClient.captured_payloads.append(json)
        return _FakeResponse(_FakeAsyncClient.next_response_data)


class _FakeErrorResponse:
    """Simulates a non-2xx response carrying a Google-style JSON error body."""

    def __init__(self, status_code: int, body: bytes):
        self._body = body
        request = httpx.Request("POST", "https://example.invalid")
        self.status_code = status_code
        self._real = httpx.Response(status_code, request=request, content=body)

    def raise_for_status(self) -> None:
        self._real.raise_for_status()

    async def aread(self) -> bytes:
        return self._body


class _FakeErroringAsyncClient(_FakeAsyncClient):
    async def post(self, url: str, json: Any = None) -> _FakeErrorResponse:
        _FakeAsyncClient.captured_urls.append(url)
        return _FakeErrorResponse(
            400, b'{"error": {"message": "models/gemini-3.1-flash is not found"}}'
        )


class _FakeStreamResponse:
    def __init__(self, lines: list[str]):
        self._lines = lines

    def raise_for_status(self) -> None:
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamContext:
    def __init__(self, lines: list[str]):
        self._lines = lines

    async def __aenter__(self) -> _FakeStreamResponse:
        return _FakeStreamResponse(self._lines)

    async def __aexit__(self, *args) -> bool:
        return False


class _FakeStreamingAsyncClient(_FakeAsyncClient):
    # A real captured SSE trace: the model calls a function on the first
    # chunk, then closes with a final chunk whose only part is empty text
    # (Gemini always sends a closing chunk; when the turn was a pure
    # function call there's no trailing text, just "").
    sse_lines: list[str] = []

    def stream(self, method: str, url: str, json: Any = None) -> _FakeStreamContext:
        _FakeAsyncClient.captured_urls.append(url)
        return _FakeStreamContext(_FakeStreamingAsyncClient.sse_lines)


@pytest.fixture(autouse=True)
def _fake_httpx(monkeypatch):
    _FakeAsyncClient.captured_urls = []
    _FakeAsyncClient.captured_payloads = []
    _FakeAsyncClient.next_response_data = {"candidates": []}
    monkeypatch.setattr(gemini_adapter_module.httpx, "AsyncClient", _FakeAsyncClient)


@pytest.mark.asyncio
async def test_constructor_resolved_key_wins_over_an_unresolved_placeholder_in_config():
    # provider_clients.py already resolved "${GEMINI_API_KEY}" to this real
    # value before constructing the adapter -- the still-literal placeholder
    # sitting in the per-call config dict must not override it.
    adapter = GeminiAdapter(api_key="real-resolved-key")

    await adapter.generate(
        messages=[{"role": "user", "content": "hi"}],
        config={"api_key": "${GEMINI_API_KEY}"},
    )

    url = _FakeAsyncClient.captured_urls[0]
    assert "key=real-resolved-key" in url
    assert "GEMINI_API_KEY" not in url


@pytest.mark.asyncio
async def test_falls_back_to_config_api_key_when_constructor_key_absent():
    adapter = GeminiAdapter(api_key=None)

    await adapter.generate(
        messages=[{"role": "user", "content": "hi"}],
        config={"api_key": "config-key"},
    )

    assert "key=config-key" in _FakeAsyncClient.captured_urls[0]


@pytest.mark.asyncio
async def test_streaming_extracts_a_function_call_from_a_real_captured_sse_trace(monkeypatch):
    # Live bug: a streamed turn that calls a function (the overwhelming
    # majority of real home-automation queries) always came back with
    # tool_calls == [] and content == "" -- i.e. the app printed "None" for
    # every prompt. _stream_generate_content wrapped the raw per-chunk
    # response objects directly as {"candidates": chunks}, but each chunk
    # IS a full top-level response (its own "candidates" list nested
    # inside), not a candidate -- so parse_tool_calls's
    # chunk.get("content", {}) always found nothing. This is the exact
    # two-chunk SSE trace Gemini sends for a pure function-call turn,
    # captured directly from the live API.
    monkeypatch.setattr(gemini_adapter_module.httpx, "AsyncClient", _FakeStreamingAsyncClient)
    _FakeStreamingAsyncClient.sse_lines = [
        'data: {"candidates": [{"content": {"parts": [{"functionCall": '
        '{"name": "get_property","args": {"device_id": "all"},"id": "call_1"}}],'
        '"role": "model"},"index": 0}]}',
        'data: {"candidates": [{"content": {"parts": [{"text": ""}],"role": "model"},'
        '"finishReason": "STOP","index": 0}]}',
    ]
    adapter = GeminiAdapter(api_key="real-resolved-key")
    streamed_chunks: list[str] = []

    async def stream_handler(text: str) -> None:
        streamed_chunks.append(text)

    result = await adapter.generate(
        messages=[{"role": "user", "content": "How many devices do I have?"}],
        config={"stream": True, "stream_handler": stream_handler},
        tools=[{"functionDeclarations": [{"name": "get_property", "description": "d", "parameters": {}}]}],
    )

    assert result["tool_calls"] == [
        {"type": "tool_use", "id": "call_1", "name": "get_property", "input": {"device_id": "all"}}
    ]


def test_build_tool_round_trip_messages_echoes_the_native_part_with_thought_signature():
    # Live bug: AgenticLoop overwrites ToolCall.raw with the provider-agnostic
    # canonical tool_use dict before build_tool_round_trip_messages ever sees
    # it, losing Gemini's own "args" key name and, critically, the sibling
    # "thoughtSignature" Gemini 3 attaches to each functionCall part --
    # live-verified against the API: a functionCall echoed back without its
    # original thoughtSignature is rejected outright ("Function call is
    # missing a thought_signature ... required for tools to work correctly").
    native_part = {
        "functionCall": {"name": "get_property", "args": {"device_id": "all"}, "id": "call_1"},
        "thoughtSignature": "opaque-signature",
    }
    raw_response = {"content": "", "native_tool_calls": [native_part]}
    # The canonical shape AgenticLoop actually hands back as tool_calls[i].raw
    # -- deliberately NOT the native part, to prove the method doesn't rely on it.
    tool_call = ToolCall(
        call_id="call_1",
        name="get_property",
        args={"device_id": "all"},
        provider="",
        raw={"type": "tool_use", "id": "call_1", "name": "get_property", "input": {"device_id": "all"}},
    )

    messages = GeminiAdapter().build_tool_round_trip_messages(
        raw_response=raw_response,
        tool_calls=[tool_call],
        tool_results=["12 devices"],
    )

    assistant_message, tool_message = messages
    assert assistant_message == {"role": "assistant", "content": None, "gemini_parts": [native_part]}
    assert tool_message == {
        "role": "tool",
        "content": "12 devices",
        "gemini_parts": [
            {"functionResponse": {"name": "get_property", "id": "call_1", "response": {"result": "12 devices"}}}
        ],
    }


@pytest.mark.asyncio
async def test_round_trip_messages_produce_a_valid_user_role_turn_on_the_next_call():
    # End-to-end: feed build_tool_round_trip_messages's own output back into
    # generate() as history, and confirm the outgoing payload is exactly the
    # shape live-verified against the real API -- a "model" turn with the
    # untouched native part (thoughtSignature included) and a "user" role
    # turn (not the dedicated "function" role) carrying the functionResponse.
    # Live bug: "function" is REJECTED outright by whichever model
    # "gemini-flash-latest" currently resolves to ("Role 'function' is not
    # supported"), even though it's accepted by gemini-3.1-flash-lite --
    # "user" is the only role verified to work on both, so it's the only
    # safe choice given the model is a runtime config value.
    native_part = {
        "functionCall": {"name": "get_property", "args": {"device_id": "all"}, "id": "call_1"},
        "thoughtSignature": "opaque-signature",
    }
    raw_response = {"content": "", "native_tool_calls": [native_part]}
    tool_call = ToolCall(call_id="call_1", name="get_property", args={"device_id": "all"}, provider="", raw={})
    adapter = GeminiAdapter(api_key="k")

    round_trip = adapter.build_tool_round_trip_messages(
        raw_response=raw_response, tool_calls=[tool_call], tool_results=["12 devices"]
    )
    await adapter.generate(
        messages=[{"role": "user", "content": "How many devices do I have?"}, *round_trip],
        config={},
    )

    contents = _FakeAsyncClient.captured_payloads[0]["contents"]
    assert contents[1] == {"role": "model", "parts": [native_part]}
    assert contents[2] == {
        "role": "user",
        "parts": [
            {"functionResponse": {"name": "get_property", "id": "call_1", "response": {"result": "12 devices"}}}
        ],
    }


def test_export_tools_strips_additional_properties_gemini_rejects_outright():
    # Live bug: Gemini's function-declaration Schema proto has no
    # additionalProperties field at all -- sending it 400s the whole request,
    # unlike most JSON Schema consumers which just ignore unknown keywords.
    # tool_diagnostics_run_step.json's "params" is deliberately free-form
    # (additionalProperties: true) for OpenAI's strict-mode escape hatch.
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_diagnostics_run_step.json")

    tools = GeminiAdapter().export_tools([spec])

    params_schema = tools[0]["functionDeclarations"][0]["parameters"]["properties"]["params"]
    assert "additionalProperties" not in params_schema


@pytest.mark.asyncio
async def test_a_400_response_body_is_included_in_the_raised_error(monkeypatch):
    # Live bug: response.raise_for_status() alone never surfaces Google's
    # actual error message (e.g. "model not found"), so every 400 looked
    # identical from the caller's side no matter the real cause.
    monkeypatch.setattr(gemini_adapter_module.httpx, "AsyncClient", _FakeErroringAsyncClient)
    adapter = GeminiAdapter(api_key="real-resolved-key")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert "gemini-3.1-flash is not found" in str(exc_info.value)


def test_export_tools_rewrites_a_nullable_type_union_to_a_plain_type_plus_nullable():
    # Live bug: nearly every optional parameter across these tool files is
    # authored as "type": [<type>, "null"] (the JSON-Schema nullable idiom).
    # Gemini's "type" is a singular enum field, not repeated -- sending a
    # list 400s the whole request ("Proto field is not repeating, cannot
    # start list").
    spec = LLMAdapter.tools_spec_from_dict(
        {
            "name": "t",
            "description": "d",
            "input_schema": {
                "type": "object",
                "properties": {"a": {"type": ["string", "null"], "description": "d"}},
            },
        }
    )

    tools = GeminiAdapter().export_tools([spec])

    a_schema = tools[0]["functionDeclarations"][0]["parameters"]["properties"]["a"]
    assert a_schema["type"] == "string"
    assert a_schema["nullable"] is True


def test_export_tools_drops_type_entirely_for_a_genuine_multi_type_union():
    # tool_device_send_command.json's "value" can genuinely be a number,
    # string, object, or null -- there's no single Gemini type for that, so
    # the field is left unconstrained (no "type" key) rather than picking an
    # arbitrary, misleading one.
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_device_send_command.json")

    tools = GeminiAdapter().export_tools([spec])

    value_schema = tools[0]["functionDeclarations"][0]["parameters"]["properties"]["value"]
    assert "type" not in value_schema
    assert value_schema["nullable"] is True


def test_export_tools_drops_properties_left_over_from_a_dropped_object_type():
    # Live bug: "value" also carries an object-shaped "properties" hint
    # ({"on": ..., "off": ...}) for its object case. Once "type" is dropped
    # above (no single type fits number|string|object), Gemini rejects the
    # leftover "properties" outright: "...properties: only allowed for
    # OBJECT type".
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_device_send_command.json")

    tools = GeminiAdapter().export_tools([spec])

    value_schema = tools[0]["functionDeclarations"][0]["parameters"]["properties"]["value"]
    assert "properties" not in value_schema


def test_export_tools_drops_a_non_string_enum_gemini_cannot_represent():
    # Live bug: tool_variables_list.json's "type" param is `{"type":
    # ["integer", "null"], "enum": [1, 2, null]}` -- Gemini's enum field only
    # supports string values on a string-typed schema, and rejected the
    # request with "Invalid value at '...enum[0]' (TYPE_STRING), 1".
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_variables_list.json")

    tools = GeminiAdapter().export_tools([spec])

    type_schema = tools[0]["functionDeclarations"][0]["parameters"]["properties"]["type"]
    assert type_schema["type"] == "integer"
    assert type_schema["nullable"] is True
    assert "enum" not in type_schema


def test_export_tools_keeps_a_string_enum_of_string_values():
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_pair_device.json")

    tools = GeminiAdapter().export_tools([spec])

    protocol_schema = tools[0]["functionDeclarations"][0]["parameters"]["properties"]["protocol"]
    assert protocol_schema["type"] == "string"
    assert protocol_schema["enum"] == ["insteon", "zwave", "zigbee", "matter", "x10"]


def test_all_tool_files_export_cleanly_for_gemini_with_no_leftover_type_lists():
    # Guard against a future tool file reintroducing a "type" list, a
    # non-string enum, or an object/array-only key stranded on a
    # differently-typed node -- any of which would 400 the whole Gemini
    # request again.
    def _assert_no_type_lists_or_bad_enums(node: Any) -> None:
        if isinstance(node, dict):
            assert not isinstance(node.get("type"), list), node
            if "enum" in node:
                assert node.get("type") == "string", node
                assert all(isinstance(v, str) for v in node["enum"]), node
            if node.get("type") != "object":
                assert "properties" not in node, node
                assert "required" not in node, node
            if node.get("type") != "array":
                assert "items" not in node, node
            for value in node.values():
                _assert_no_type_lists_or_bad_enums(value)
        elif isinstance(node, list):
            for item in node:
                _assert_no_type_lists_or_bad_enums(item)

    adapter = GeminiAdapter()
    for tool_file in sorted(_TOOLS_DIR.glob("*.json")):
        spec = LLMAdapter.tools_spec_from_file(tool_file)
        tools = adapter.export_tools([spec])
        _assert_no_type_lists_or_bad_enums(tools[0]["functionDeclarations"][0]["parameters"])


def test_export_tools_leaves_the_rest_of_the_schema_untouched():
    spec = LLMAdapter.tools_spec_from_dict(
        {
            "name": "t",
            "description": "d",
            "input_schema": {
                "type": "object",
                "required": ["a"],
                "properties": {"a": {"type": "string", "description": "d"}},
            },
        }
    )

    tools = GeminiAdapter().export_tools([spec])

    assert tools[0]["functionDeclarations"][0]["parameters"] == spec.json_schema

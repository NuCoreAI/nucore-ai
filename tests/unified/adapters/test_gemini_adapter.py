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

import logging
import time
from pathlib import Path
from typing import Any

import pytest

import httpx

from unified.adapters.base_adapter import LLMAdapter, ToolCall
from unified.adapters import gemini_adapter as gemini_adapter_module
from unified.adapters.gemini_adapter import GeminiAdapter

_TOOLS_DIR = Path(__file__).parents[3] / "src" / "unified" / "tools"
_PLUGIN_AUTHORING_TOOLS_DIR = Path(__file__).parents[3] / "src" / "unified" / "plugin_authoring" / "tools"


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


@pytest.mark.asyncio
async def test_generate_surfaces_usage_metadata_non_streaming():
    # cachedContentTokenCount is Gemini's automatic/implicit-caching hit
    # count (2.5+ models, no cache_control needed) -- surfaced the same way
    # as Claude's/OpenAI's usage so AgenticLoop's prompt-log write doesn't
    # need to know which provider produced it.
    _FakeAsyncClient.next_response_data = {
        "candidates": [],
        "usageMetadata": {"promptTokenCount": 500, "candidatesTokenCount": 20, "cachedContentTokenCount": 420},
    }
    adapter = GeminiAdapter(api_key="k")

    result = await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert result["usage"] == {"promptTokenCount": 500, "candidatesTokenCount": 20, "cachedContentTokenCount": 420}


@pytest.mark.asyncio
async def test_generate_usage_defaults_to_empty_dict_when_absent():
    _FakeAsyncClient.next_response_data = {"candidates": []}
    adapter = GeminiAdapter(api_key="k")

    result = await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert result["usage"] == {}


@pytest.mark.asyncio
async def test_streaming_generate_surfaces_usage_from_the_final_sse_chunk(monkeypatch):
    # usageMetadata is cumulative -- only the last chunk that carries one has
    # the final counts, matching how Gemini's real SSE trace reports it.
    monkeypatch.setattr(gemini_adapter_module.httpx, "AsyncClient", _FakeStreamingAsyncClient)
    _FakeStreamingAsyncClient.sse_lines = [
        'data: {"candidates": [{"content": {"parts": [{"text": "hi"}],"role": "model"}}],'
        '"usageMetadata": {"promptTokenCount": 100, "cachedContentTokenCount": 0}}',
        'data: {"candidates": [{"content": {"parts": [{"text": ""}],"role": "model"},'
        '"finishReason": "STOP"}],'
        '"usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 3, "cachedContentTokenCount": 80}}',
    ]
    adapter = GeminiAdapter(api_key="real-resolved-key")

    async def stream_handler(text: str) -> None:
        pass

    result = await adapter.generate(
        messages=[{"role": "user", "content": "hi"}],
        config={"stream": True, "stream_handler": stream_handler},
    )

    assert result["usage"] == {"promptTokenCount": 100, "candidatesTokenCount": 3, "cachedContentTokenCount": 80}


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
    # tool_plugin_call.json's "args" is deliberately free-form
    # (additionalProperties: true) for OpenAI's strict-mode escape hatch.
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_plugin_call.json")

    tools = GeminiAdapter().export_tools([spec])

    params_schema = tools[0]["functionDeclarations"][0]["parameters"]["properties"]["args"]
    assert "additionalProperties" not in params_schema


@pytest.mark.asyncio
async def test_a_400_response_body_is_included_in_the_raised_error(monkeypatch):
    # Live bug: response.raise_for_status() alone never surfaces Google's
    # actual error message (e.g. "model not found"), so every 400 looked
    # identical from the caller's side no matter the real cause.
    # A 400 is not retried: one request, no sleep.
    slept: list[float] = []

    async def _sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(gemini_adapter_module.asyncio, "sleep", _sleep)
    monkeypatch.setattr(gemini_adapter_module.httpx, "AsyncClient", _FakeErroringAsyncClient)
    adapter = GeminiAdapter(api_key="real-resolved-key")

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert "gemini-3.1-flash is not found" in str(exc_info.value)
    assert slept == []
    assert not getattr(exc_info.value, "retries_exhausted", False)


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
    # tool_device_send_command.json's per-entry "value" (nested under
    # commands[].value) can genuinely be a number, string, object, or null --
    # there's no single Gemini type for that, so the field is left
    # unconstrained (no "type" key) rather than picking an arbitrary,
    # misleading one.
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_device_send_command.json")

    tools = GeminiAdapter().export_tools([spec])

    commands_schema = tools[0]["functionDeclarations"][0]["parameters"]["properties"]["commands"]
    value_schema = commands_schema["items"]["properties"]["value"]
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

    commands_schema = tools[0]["functionDeclarations"][0]["parameters"]["properties"]["commands"]
    value_schema = commands_schema["items"]["properties"]["value"]
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


def test_export_tools_keeps_a_parameter_literally_named_properties():
    # Live bug: get_property's own top-level parameter is itself named
    # "properties" ({"type": "object", "required": ["properties"],
    # "properties": {"properties": {...}}}). The sanitizer's recursion
    # mistook that "properties" map (keyed by arbitrary parameter names) for
    # a schema node in its own right, found no "type" key on it, and -- via
    # the "strip properties/required when the resolved type doesn't match"
    # logic meant for real schema nodes -- deleted the "properties" entry
    # from the map outright. That left "required": ["properties"] dangling
    # with no matching entry, which Gemini 400'd on: "parameters.
    # required[0]: property is not defined".
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_device_get_property.json")

    tools = GeminiAdapter().export_tools([spec])

    params_schema = tools[0]["functionDeclarations"][0]["parameters"]
    assert "properties" in params_schema["properties"]
    for required_name in params_schema.get("required", []):
        assert required_name in params_schema["properties"], params_schema


def test_export_tools_keeps_an_optional_parameter_literally_named_properties():
    # Same collision as above, but with no "required" entry at all --
    # get_device_history's "properties" parameter is optional, so the bug
    # here never 400'd; it silently dropped the whole parameter instead,
    # making it impossible for Gemini to ever be told to use it.
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_device_get_history.json")

    tools = GeminiAdapter().export_tools([spec])

    params_schema = tools[0]["functionDeclarations"][0]["parameters"]
    assert "properties" in params_schema["properties"]


def test_all_tool_files_export_cleanly_for_gemini_with_no_leftover_type_lists():
    # Guard against a future tool file reintroducing a "type" list, a
    # non-string enum, or an object/array-only key stranded on a
    # differently-typed node -- any of which would 400 the whole Gemini
    # request again. Covers both tool directories.
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
            for key, value in node.items():
                # A "properties" map's own keys are arbitrary tool-parameter
                # names (e.g. get_property's own "properties" parameter),
                # not schema keywords -- walking into the map itself with
                # this same node-shaped check would misread a parameter
                # named "properties"/"required"/etc. as a leftover keyword.
                # Only its *values* are schema nodes.
                if key == "properties" and isinstance(value, dict):
                    for sub_schema in value.values():
                        _assert_no_type_lists_or_bad_enums(sub_schema)
                else:
                    _assert_no_type_lists_or_bad_enums(value)
        elif isinstance(node, list):
            for item in node:
                _assert_no_type_lists_or_bad_enums(item)

    adapter = GeminiAdapter()
    tool_files = sorted(_TOOLS_DIR.glob("*.json")) + sorted(_PLUGIN_AUTHORING_TOOLS_DIR.glob("*.json"))
    for tool_file in tool_files:
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


_OVERLOADED = b'{"error": {"message": "The model is overloaded. Please try again later."}}'
_SECRET_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/m:generateContent?key=super-secret"
)


class _SequencedResponse:
    """One scripted HTTP response. The request URL embeds an API key so a log
    of the raw httpx error fails the redaction assertions."""

    def __init__(self, status_code: int, body: bytes = b"", data: dict[str, Any] | None = None, headers: dict[str, str] | None = None, lines: list[str] | None = None):
        self.status_code = status_code
        self.headers = headers or {}
        self._body = body
        self._data = data or {}
        self._lines = lines or []
        request = httpx.Request("POST", _SECRET_URL)
        self._real = httpx.Response(status_code, request=request, content=body, headers=self.headers)

    def raise_for_status(self) -> None:
        self._real.raise_for_status()

    async def aread(self) -> bytes:
        return self._body

    def json(self) -> dict[str, Any]:
        return self._data

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _SequencedContext:
    def __init__(self, response: _SequencedResponse):
        self._response = response

    async def __aenter__(self) -> _SequencedResponse:
        return self._response

    async def __aexit__(self, *args) -> bool:
        return False


class _SequencedClient(_FakeAsyncClient):
    script: list[_SequencedResponse] = []

    async def post(self, url: str, json: Any = None) -> _SequencedResponse:
        _FakeAsyncClient.captured_urls.append(url)
        _FakeAsyncClient.captured_payloads.append(json)
        return _SequencedClient.script.pop(0)

    def stream(self, method: str, url: str, json: Any = None) -> _SequencedContext:
        _FakeAsyncClient.captured_urls.append(url)
        return _SequencedContext(_SequencedClient.script.pop(0))


def _no_blocking_sleep(monkeypatch) -> list[float]:
    slept: list[float] = []

    async def _sleep(delay: float) -> None:
        slept.append(delay)

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("time.sleep")

    monkeypatch.setattr(gemini_adapter_module.asyncio, "sleep", _sleep)
    monkeypatch.setattr(gemini_adapter_module.random, "uniform", lambda _a, _b: 0.0)
    monkeypatch.setattr(time, "sleep", _forbidden)
    return slept


def _ok_response(text: str = "done") -> _SequencedResponse:
    return _SequencedResponse(
        200,
        data={"candidates": [{"content": {"parts": [{"text": text}]}}]},
    )


@pytest.mark.asyncio
async def test_503_then_200_retries_once_and_returns_the_body(monkeypatch, caplog):
    slept = _no_blocking_sleep(monkeypatch)
    _SequencedClient.script = [_SequencedResponse(503, _OVERLOADED), _ok_response()]
    monkeypatch.setattr(gemini_adapter_module.httpx, "AsyncClient", _SequencedClient)
    adapter = GeminiAdapter(api_key="real-resolved-key")

    with caplog.at_level(logging.WARNING):
        result = await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert result["content"] == "done"
    assert slept == [2.0]
    assert len(_FakeAsyncClient.captured_urls) == 2
    assert "super-secret" not in caplog.text
    assert "key=" not in caplog.text
    assert "real-resolved-key" not in caplog.text


@pytest.mark.asyncio
async def test_four_503s_abort_after_three_sleeps(monkeypatch, caplog):
    slept = _no_blocking_sleep(monkeypatch)
    _SequencedClient.script = [_SequencedResponse(503, _OVERLOADED) for _ in range(4)]
    monkeypatch.setattr(gemini_adapter_module.httpx, "AsyncClient", _SequencedClient)
    adapter = GeminiAdapter(api_key="real-resolved-key")

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert slept == [2.0, 4.0, 8.0]
    assert len(_FakeAsyncClient.captured_urls) == 4
    assert getattr(exc_info.value, "retries_exhausted", False) is True
    assert "Gemini API unreachable after max retries due to high demand. Automation turn aborted." in caplog.text
    assert "super-secret" not in caplog.text
    assert "key=" not in caplog.text
    assert "real-resolved-key" not in caplog.text


@pytest.mark.asyncio
async def test_retry_after_replaces_the_backoff_and_stays_inside_the_cap(monkeypatch):
    slept = _no_blocking_sleep(monkeypatch)
    _SequencedClient.script = [
        _SequencedResponse(503, _OVERLOADED, headers={"Retry-After": "1"}),
        _ok_response(),
    ]
    monkeypatch.setattr(gemini_adapter_module.httpx, "AsyncClient", _SequencedClient)
    adapter = GeminiAdapter(api_key="real-resolved-key")

    result = await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert result["content"] == "done"
    assert slept == [1.0]


@pytest.mark.asyncio
async def test_jitter_cannot_push_cumulative_sleep_past_14_seconds(monkeypatch):
    slept = _no_blocking_sleep(monkeypatch)
    monkeypatch.setattr(gemini_adapter_module.random, "uniform", lambda _a, _b: 0.2)
    _SequencedClient.script = [_SequencedResponse(503, _OVERLOADED) for _ in range(4)]
    monkeypatch.setattr(gemini_adapter_module.httpx, "AsyncClient", _SequencedClient)
    adapter = GeminiAdapter(api_key="real-resolved-key")

    with pytest.raises(httpx.HTTPStatusError):
        await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert slept == pytest.approx([2.4, 4.8, 6.8])
    assert sum(slept) == pytest.approx(14.0)


@pytest.mark.asyncio
async def test_stream_503_retries_the_stream_and_does_not_call_generate_content(monkeypatch):
    slept = _no_blocking_sleep(monkeypatch)
    lines = ['data: {"candidates": [{"content": {"parts": [{"text": "ok"}], "role": "model"}}]}']
    _SequencedClient.script = [
        _SequencedResponse(503, _OVERLOADED),
        _SequencedResponse(200, lines=lines),
    ]
    monkeypatch.setattr(gemini_adapter_module.httpx, "AsyncClient", _SequencedClient)
    adapter = GeminiAdapter(api_key="real-resolved-key")

    async def stream_handler(_text: str) -> None:
        return None

    result = await adapter.generate(
        messages=[{"role": "user", "content": "hi"}],
        config={"stream": True, "stream_handler": stream_handler},
    )

    assert result["content"] == "ok"
    assert slept == [2.0]
    assert _FakeAsyncClient.captured_urls
    assert all("streamGenerateContent" in url for url in _FakeAsyncClient.captured_urls)
    assert all(":generateContent?" not in url for url in _FakeAsyncClient.captured_urls)


@pytest.mark.asyncio
async def test_non_retryable_stream_error_falls_back_to_generate_content_once(monkeypatch):
    slept = _no_blocking_sleep(monkeypatch)
    _SequencedClient.script = [
        _SequencedResponse(400, b'{"error": {"message": "stream endpoint unsupported"}}'),
        _ok_response("from-generate"),
    ]
    monkeypatch.setattr(gemini_adapter_module.httpx, "AsyncClient", _SequencedClient)
    adapter = GeminiAdapter(api_key="real-resolved-key")

    async def stream_handler(_text: str) -> None:
        return None

    result = await adapter.generate(
        messages=[{"role": "user", "content": "hi"}],
        config={"stream": True, "stream_handler": stream_handler},
    )

    assert result["content"] == "from-generate"
    assert slept == []
    assert "streamGenerateContent" in _FakeAsyncClient.captured_urls[0]
    assert ":generateContent?" in _FakeAsyncClient.captured_urls[1]

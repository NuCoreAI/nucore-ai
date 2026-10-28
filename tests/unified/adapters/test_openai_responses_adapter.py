"""OpenAIResponsesAdapter -- the ``/v1/responses`` counterpart to
``OpenAIAdapter``, for reasoning-tier models that reject function tools
under every ``reasoning_effort`` value on ``chat.completions`` (see
design/migration-to-openai-response-api.md for the full investigation).

Mirrors test_openai_adapter.py's conventions (monkeypatching
``adapter._client.responses.create``, ``SimpleNamespace`` response doubles)
but against the Responses shape: flat tool definitions, ``input``/
``instructions`` instead of ``messages``, nested ``reasoning``, and a typed
streaming event union instead of ``ChatCompletionChunk``s.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from openai import BadRequestError

from unified.adapters.base_adapter import ToolCall, ToolSpec
from unified.adapters.openai_responses_adapter import OpenAIResponsesAdapter


def _adapter() -> OpenAIResponsesAdapter:
    return OpenAIResponsesAdapter(api_key="test-key")


def _item(data: dict[str, Any]) -> SimpleNamespace:
    ns = SimpleNamespace(**data)
    ns.model_dump = lambda: data
    return ns


def _fake_response(*, output: list[SimpleNamespace] | None = None, output_text: str = "", usage: dict | None = None):
    usage_obj = SimpleNamespace(model_dump=lambda: usage) if usage is not None else None
    return SimpleNamespace(output=output or [], output_text=output_text, usage=usage_obj, model_dump=lambda: {})


def _unrelated_bad_request_error() -> BadRequestError:
    inner = {"message": "max_output_tokens is too large.", "type": "invalid_request_error", "param": "max_output_tokens", "code": None}
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(400, request=request, json={"error": inner})
    return BadRequestError("bad request", response=response, body=inner)


def test_export_tools_produces_flat_shape_with_strict_normalization():
    adapter = _adapter()
    spec = ToolSpec(
        name="t",
        description="d",
        json_schema={"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
        strict=True,
    )
    tools = adapter.export_tools([spec])

    assert tools == [
        {
            "type": "function",
            "name": "t",
            "description": "d",
            "parameters": {
                "type": "object",
                "properties": {"a": {"type": "string"}},
                "required": ["a"],
                "additionalProperties": False,
            },
            "strict": True,
        }
    ]
    # Confirm it's the flat shape, not chat.completions' nested
    # {"type": "function", "function": {...}}.
    assert "function" not in tools[0]


@pytest.mark.asyncio
async def test_non_streaming_generate_returns_text_only_reply():
    adapter = _adapter()

    async def fake_create(**kwargs):
        return _fake_response(output_text="hello there")

    adapter._client.responses.create = fake_create

    result = await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={"model": "gpt-6.1-sol"})

    assert result["content"] == "hello there"
    assert result["tool_calls"] == []


@pytest.mark.asyncio
async def test_non_streaming_generate_parses_function_call_output_item():
    adapter = _adapter()
    call_item = _item(
        {"type": "function_call", "call_id": "call_1", "name": "search", "arguments": '{"q": "weather"}', "id": "fc_1"}
    )
    async def fake_create(**kwargs):
        return _fake_response(output=[call_item], output_text="")

    adapter._client.responses.create = fake_create

    result = await adapter.generate(
        messages=[{"role": "user", "content": "hi"}],
        config={"model": "gpt-6.1-sol"},
        tools=[{"type": "function", "name": "search", "description": "d", "parameters": {}, "strict": True}],
    )

    assert result["tool_calls"] == [{"type": "tool_use", "id": "call_1", "name": "search", "input": {"q": "weather"}}]


@pytest.mark.asyncio
async def test_web_search_call_output_item_is_never_surfaced_as_a_tool_call():
    adapter = _adapter()
    search_item = _item(
        {
            "type": "web_search_call",
            "id": "ws_1",
            "status": "completed",
            "action": {"type": "search", "query": "weather", "sources": [{"type": "url", "url": "https://example.com"}]},
        }
    )
    async def fake_create(**kwargs):
        return _fake_response(output=[search_item], output_text="It's sunny.")

    adapter._client.responses.create = fake_create

    result = await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={"model": "gpt-6.1-sol"})

    assert result["tool_calls"] == []
    # Still surfaced via "output" so a caller's on_raw_response hook can
    # harvest the sources -- see runtime.py's
    # _record_openai_responses_web_search_evidence.
    assert result["output"] == [search_item.model_dump()]


@pytest.mark.asyncio
async def test_reasoning_effort_configured_is_forwarded_as_nested_reasoning():
    adapter = _adapter()
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_response(output_text="ok")

    adapter._client.responses.create = fake_create

    await adapter.generate(
        messages=[{"role": "user", "content": "hi"}], config={"model": "gpt-6.1-sol", "reasoning_effort": "low"}
    )

    assert captured["reasoning"] == {"effort": "low"}


@pytest.mark.asyncio
async def test_reasoning_omitted_when_not_configured():
    adapter = _adapter()
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_response(output_text="ok")

    adapter._client.responses.create = fake_create

    await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={"model": "gpt-6.1-sol"})

    assert "reasoning" not in captured


@pytest.mark.asyncio
async def test_system_role_messages_are_joined_into_instructions_not_input():
    adapter = _adapter()
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_response(output_text="ok")

    adapter._client.responses.create = fake_create

    await adapter.generate(
        messages=[
            {"role": "system", "content": "static rules"},
            {"role": "system", "content": "volatile tail"},
            {"role": "user", "content": "hi"},
        ],
        config={"model": "gpt-6.1-sol"},
    )

    assert captured["instructions"] == "static rules\n\nvolatile tail"
    assert captured["input"] == [{"type": "message", "role": "user", "content": "hi"}]


@pytest.mark.asyncio
async def test_build_tool_round_trip_messages_round_trips_back_through_split_messages():
    adapter = _adapter()
    tool_call = ToolCall(call_id="call_1", name="search", args={"q": "weather"}, provider="openai_responses")

    round_trip = adapter.build_tool_round_trip_messages(
        raw_response={}, tool_calls=[tool_call], tool_results=["sunny"]
    )

    assert round_trip == [
        {"type": "function_call", "call_id": "call_1", "name": "search", "arguments": json.dumps({"q": "weather"})},
        {"type": "function_call_output", "call_id": "call_1", "output": "sunny"},
    ]

    # The pass-through branch in _split_messages_for_responses must
    # recognize exactly what build_tool_round_trip_messages just produced.
    instructions, input_items = adapter._split_messages_for_responses(
        [{"role": "user", "content": "weather?"}, *round_trip]
    )
    assert instructions == ""
    assert input_items == [{"type": "message", "role": "user", "content": "weather?"}, *round_trip]


@pytest.mark.asyncio
async def test_streaming_generate_forwards_text_deltas_and_captures_function_call():
    adapter = _adapter()
    call_item = _item({"type": "function_call", "call_id": "call_1", "name": "search", "arguments": "{}"})

    async def fake_stream():
        yield SimpleNamespace(type="response.output_text.delta", delta="It's ")
        yield SimpleNamespace(type="response.output_text.delta", delta="sunny.")
        yield SimpleNamespace(type="response.output_item.done", item=call_item)
        yield SimpleNamespace(type="response.completed", response=_fake_response(usage={"input_tokens": 5}))

    async def fake_create(**kwargs):
        return fake_stream()

    adapter._client.responses.create = fake_create

    chunks = []

    async def handler(text):
        chunks.append(text)

    result = await adapter.generate(
        messages=[{"role": "user", "content": "weather?"}],
        config={"model": "gpt-6.1-sol", "stream": True, "stream_handler": handler},
        tools=[{"type": "function", "name": "search", "description": "d", "parameters": {}, "strict": True}],
    )

    assert chunks == ["It's ", "sunny."]
    assert result["content"] == "It's sunny."
    assert result["tool_calls"] == [{"type": "tool_use", "id": "call_1", "name": "search", "input": {}}]
    assert result["usage"] == {"input_tokens": 5}


@pytest.mark.asyncio
async def test_unrelated_bad_request_error_propagates_unchanged():
    adapter = _adapter()

    async def fake_create(**kwargs):
        raise _unrelated_bad_request_error()

    adapter._client.responses.create = fake_create

    with pytest.raises(BadRequestError):
        await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={"model": "gpt-6.1-sol"})

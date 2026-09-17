"""OpenAIAdapter.export_tools -- strict-mode JSON Schema normalization, and
the per-tool ``"strict": false`` escape hatch for tools whose schema has a
genuinely free-form object parameter (dynamic keys the caller can't
enumerate in advance, e.g. call_plugin's ``args``).

Regression coverage for a live bug: OpenAI's strict mode requires every
object node to set ``additionalProperties: false`` with every key
enumerated in ``properties`` -- a schema that intentionally declares
``"additionalProperties": true`` (no fixed key set) can never satisfy that,
and the API rejects the whole request with a 400 if strict mode is forced on
it anyway.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from unified.adapters.base_adapter import LLMAdapter, ToolSpec
from unified.adapters.openai_adapter import OpenAIAdapter

_TOOLS_DIR = Path(__file__).parents[3] / "src" / "unified" / "tools"


def _adapter() -> OpenAIAdapter:
    return OpenAIAdapter(api_key="test-key")


def _fake_completion_response(usage: dict | None = None):
    message = SimpleNamespace(content="hi", tool_calls=None)
    usage_obj = SimpleNamespace(model_dump=lambda: usage) if usage is not None else None
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage_obj, model_dump=lambda: {})


def test_strict_true_by_default_forces_additional_properties_false():
    spec = ToolSpec(
        name="t",
        description="d",
        json_schema={
            "type": "object",
            "properties": {"a": {"type": "string"}},
        },
    )
    tools = _adapter().export_tools([spec])
    params = tools[0]["function"]["parameters"]
    assert params["additionalProperties"] is False
    assert tools[0]["function"]["strict"] is True


def test_tool_file_strict_false_key_overrides_the_batch_default():
    # tools_spec_from_files defaults every tool to strict=True; a tool file
    # can opt itself out via a top-level "strict": false key.
    spec = LLMAdapter.tools_spec_from_dict(
        {
            "name": "t",
            "description": "d",
            "strict": False,
            "input_schema": {
                "type": "object",
                "properties": {"params": {"type": "object", "additionalProperties": True}},
            },
        },
        strict=True,
    )
    assert spec.strict is False

    tools = _adapter().export_tools([spec])
    # Unnormalized -- additionalProperties: true survives exactly as authored.
    params_schema = tools[0]["function"]["parameters"]["properties"]["params"]
    assert params_schema["additionalProperties"] is True
    assert tools[0]["function"]["strict"] is False


def test_plugin_call_tool_file_is_strict_false():
    # A real tool with a genuinely free-form object (args vary per plugin
    # tool), which strict mode cannot represent -- the pattern that
    # originally triggered a live 400 from OpenAI.
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_plugin_call.json")
    assert spec.strict is False

    tools = _adapter().export_tools([spec])
    args_schema = tools[0]["function"]["parameters"]["properties"]["args"]
    assert args_schema["additionalProperties"] is True
    assert tools[0]["function"]["strict"] is False


def test_all_free_form_params_tool_files_declare_strict_false():
    # Guard against a future tool file reintroducing additionalProperties:
    # true without also opting out of strict mode.
    for name in ("tool_plugin_call.json",):
        data = json.loads((_TOOLS_DIR / name).read_text())
        assert data.get("strict") is False, name


@pytest.mark.asyncio
async def test_reasoning_effort_forwarded_when_configured():
    # Live bug: a reasoning-tier model rejected function tools on
    # chat.completions unless reasoning_effort was explicitly set (e.g. to
    # "none") -- the adapter never sent this field at all before.
    adapter = _adapter()
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_completion_response()

    adapter._client.chat.completions.create = fake_create

    await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={"reasoning_effort": "none"})

    assert captured["reasoning_effort"] == "none"


@pytest.mark.asyncio
async def test_reasoning_effort_omitted_when_not_configured():
    adapter = _adapter()
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_completion_response()

    adapter._client.chat.completions.create = fake_create

    await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert "reasoning_effort" not in captured


@pytest.mark.asyncio
async def test_consecutive_system_messages_are_merged_into_one():
    # The prompt arrives as [static, tail] system messages (see
    # prompt_builder); some OpenAI-compatible chat templates (llama.cpp)
    # reject more than one system message, so they're merged back here.
    adapter = _adapter()
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_completion_response()

    adapter._client.chat.completions.create = fake_create

    await adapter.generate(
        messages=[
            {"role": "system", "content": "static"},
            {"role": "system", "content": "tail"},
            {"role": "user", "content": "hi"},
        ],
        config={},
    )

    assert captured["messages"] == [
        {"role": "system", "content": "static\n\ntail"},
        {"role": "user", "content": "hi"},
    ]


@pytest.mark.asyncio
async def test_generate_surfaces_usage_non_streaming():
    adapter = _adapter()
    usage = {"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 80}}

    async def fake_create(**kwargs):
        return _fake_completion_response(usage=usage)

    adapter._client.chat.completions.create = fake_create

    result = await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert result["usage"] == usage


@pytest.mark.asyncio
async def test_generate_usage_defaults_to_empty_dict_when_absent():
    adapter = _adapter()

    async def fake_create(**kwargs):
        return _fake_completion_response()  # no usage= passed

    adapter._client.chat.completions.create = fake_create

    result = await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert result["usage"] == {}


@pytest.mark.asyncio
async def test_generate_forwards_extra_headers_hook_to_both_calls():
    # _extra_headers is a no-op on plain OpenAIAdapter, but subclasses (e.g.
    # GrokAdapter) override it -- verify the hook's return value actually
    # reaches chat.completions.create for both the streaming and
    # non-streaming paths, not just one.
    adapter = _adapter()
    adapter._extra_headers = lambda cfg: {"x-test": "1"}
    captured = {}

    async def fake_create(**kwargs):
        captured.update(kwargs)
        return _fake_completion_response()

    adapter._client.chat.completions.create = fake_create
    await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert captured["extra_headers"] == {"x-test": "1"}


@pytest.mark.asyncio
async def test_streaming_generate_surfaces_usage_from_final_chunk():
    adapter = _adapter()
    usage = {"prompt_tokens": 50, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 40}}

    def _chunk(content=None, usage_obj=None):
        delta = SimpleNamespace(content=content, tool_calls=None)
        choice = SimpleNamespace(delta=delta)
        return SimpleNamespace(choices=[choice], usage=usage_obj)

    async def fake_stream(**kwargs):
        assert kwargs["stream_options"] == {"include_usage": True}

        async def _gen():
            yield _chunk(content="hi")
            # OpenAI's include_usage final chunk: empty choices, usage set.
            yield SimpleNamespace(choices=[], usage=SimpleNamespace(model_dump=lambda: usage))

        return _gen()

    adapter._client.chat.completions.create = fake_stream

    async def handler(text, is_end=False):
        pass

    result = await adapter.generate(
        messages=[{"role": "user", "content": "hi"}],
        config={"stream": True, "stream_handler": handler},
    )

    assert result["usage"] == usage

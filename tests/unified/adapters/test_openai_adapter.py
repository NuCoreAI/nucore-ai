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
from typing import Any

import pytest

from unified.adapters.base_adapter import LLMAdapter, ToolSpec
from unified.adapters.openai_adapter import OpenAIAdapter

_TOOLS_DIR = Path(__file__).parents[3] / "src" / "unified" / "tools"
_PLUGIN_AUTHORING_TOOLS_DIR = Path(__file__).parents[3] / "src" / "unified" / "plugin_authoring" / "tools"


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


def _find_unconstrained_schema_nodes(node: Any, path: list[str]) -> list[tuple[str, list[str]]]:
    """Walk a tool's input_schema looking for the three shapes OpenAI strict
    mode can never satisfy, no matter what _normalize_schema_node does:
    ``additionalProperties: true`` (a genuinely free-form object), an
    array-typed node with no ``items`` at all (a genuinely untyped item),
    and an object-typed node with neither ``properties`` nor
    ``additionalProperties`` declared at all -- in plain JSON Schema this
    means "any shape accepted," functionally identical to
    ``additionalProperties: true`` even though the keyword itself is
    absent (live bug: validate_profile's `profile` field -- genuinely
    polymorphic by design, "either the wire shape... or the catalog
    shape," so there's no fixed property set to declare). All three are
    valid, legal plain JSON Schema -- strict mode simply cannot represent
    any of them, at any nesting depth, which is exactly why this needs its
    own real scan rather than trusting the normalizer to paper over it
    (see the three live 400s this file guards against)."""
    if not isinstance(node, dict):
        return []
    findings: list[tuple[str, list[str]]] = []
    if node.get("additionalProperties") is True:
        findings.append(("additionalProperties: true", path))
    node_type = node.get("type")
    type_list = node_type if isinstance(node_type, list) else [node_type]
    if "array" in type_list and "items" not in node:
        findings.append(("array missing items", path))
    if "object" in type_list and "properties" not in node and "additionalProperties" not in node:
        findings.append(("object with no properties or additionalProperties", path))
    items = node.get("items")
    if isinstance(items, dict):
        findings.extend(_find_unconstrained_schema_nodes(items, path + ["items"]))
    elif isinstance(items, list):
        for index, item in enumerate(items):
            findings.extend(_find_unconstrained_schema_nodes(item, path + [f"items[{index}]"]))
    properties = node.get("properties")
    if isinstance(properties, dict):
        for key, value in properties.items():
            findings.extend(_find_unconstrained_schema_nodes(value, path + [key]))
    for keyword in ("anyOf", "oneOf", "allOf"):
        variants = node.get(keyword)
        if isinstance(variants, list):
            for index, variant in enumerate(variants):
                findings.extend(_find_unconstrained_schema_nodes(variant, path + [f"{keyword}[{index}]"]))
    return findings


def test_every_tool_file_with_an_unconstrained_schema_node_declares_strict_false():
    # Guard against a future tool file reintroducing additionalProperties:
    # true, an array typed with no fixed item type, or an object typed with
    # neither properties nor additionalProperties declared -- anywhere in
    # its schema, at any nesting depth -- without also opting out of strict
    # mode. Covers both tool directories, not just the files already known
    # to need this -- deeply-nested cases like
    # tool_generate_plugin_scaffold.json's aiTools[].params, or
    # tool_device_send_command.json's commands[].values[].value, are
    # exactly what a flat allowlist would miss.
    tool_files = sorted(_TOOLS_DIR.glob("tool_*.json")) + sorted(_PLUGIN_AUTHORING_TOOLS_DIR.glob("tool_*.json"))
    for path in tool_files:
        data = json.loads(path.read_text())
        findings = _find_unconstrained_schema_nodes(data.get("input_schema", {}), [])
        if findings:
            assert data.get("strict") is False, f"{path.name}: {findings}"


def test_array_type_missing_items_is_left_unnormalized_under_strict_mode():
    # Regression guard for a live bug in the other direction: an earlier fix
    # attempt defaulted a missing "items" to {} under strict mode, which
    # OpenAI rejects just as hard ("schema must have a 'type' key" -- {}
    # itself has no type). There is no default that satisfies strict mode
    # for a genuinely untyped item, so _normalize_schema_node must NOT touch
    # an array missing items at all -- the fix belongs on the tool file
    # (declare "strict": false) instead, not here.
    spec = ToolSpec(
        name="t",
        description="d",
        json_schema={
            "type": "object",
            "properties": {"config": {"type": ["object", "array"]}},
        },
    )
    tools = _adapter().export_tools([spec])
    config_schema = tools[0]["function"]["parameters"]["properties"]["config"]
    assert "items" not in config_schema


def test_array_with_existing_items_is_still_normalized():
    # An array that already specifies a real items schema (the shape
    # tool_device_get_history.json/tool_device_send_command.json use) is
    # unaffected and still recursed into normally.
    spec = ToolSpec(
        name="t",
        description="d",
        json_schema={
            "type": "object",
            "properties": {"tags": {"type": "array", "items": {"type": "string"}}},
        },
    )
    tools = _adapter().export_tools([spec])
    tags_schema = tools[0]["function"]["parameters"]["properties"]["tags"]
    assert tags_schema["items"] == {"type": "string"}


def test_configure_plugin_tool_file_is_strict_false():
    # End-to-end regression coverage for the real production bug:
    # "config"'s type union (["object", "array"]) has no fixed item type for
    # its array branch -- genuinely unconstrained, the same category of
    # problem as call_plugin's free-form "args" -- so the tool file opts out
    # of strict mode entirely rather than being silently (and incorrectly)
    # patched at export time.
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_plugin_configure.json")
    assert spec.strict is False

    tools = _adapter().export_tools([spec])
    # Unnormalized -- authored exactly as written, no "items"/
    # "additionalProperties" injected.
    config_schema = tools[0]["function"]["parameters"]["properties"]["config"]
    assert config_schema == {
        "type": ["object", "array"],
        "description": config_schema["description"],
    }
    assert tools[0]["function"]["strict"] is False


def test_generate_plugin_scaffold_tool_file_is_strict_false():
    # End-to-end regression coverage for a second live production bug:
    # server_entry.aiTools[].params is a verbatim-passthrough, caller-
    # defined JSON-Schema-shaped spec -- additionalProperties: true at
    # arbitrary nesting depth, not just a flat free-form object -- so this
    # tool opts out of strict mode entirely too.
    spec = LLMAdapter.tools_spec_from_file(_PLUGIN_AUTHORING_TOOLS_DIR / "tool_generate_plugin_scaffold.json")
    assert spec.strict is False

    tools = _adapter().export_tools([spec])
    params_schema = (
        tools[0]["function"]["parameters"]["properties"]["server_entry"]["properties"]["aiTools"]["items"][
            "properties"
        ]["params"]
    )
    # Unnormalized -- additionalProperties: true survives exactly as authored.
    assert params_schema["additionalProperties"] is True
    assert tools[0]["function"]["strict"] is False


def test_validate_profile_tool_file_is_strict_false():
    # End-to-end regression coverage for a third live production bug:
    # "profile" is a genuinely polymorphic field (the wire shape or the
    # catalog shape, per the tool's own description) with neither
    # properties nor additionalProperties declared -- implicitly "any
    # shape accepted" in plain JSON Schema, exactly as unrepresentable in
    # strict mode as an explicit additionalProperties: true.
    spec = LLMAdapter.tools_spec_from_file(_PLUGIN_AUTHORING_TOOLS_DIR / "tool_validate_profile.json")
    assert spec.strict is False

    tools = _adapter().export_tools([spec])
    profile_schema = tools[0]["function"]["parameters"]["properties"]["profile"]
    assert "properties" not in profile_schema
    assert "additionalProperties" not in profile_schema
    assert tools[0]["function"]["strict"] is False


def test_device_send_command_tool_file_is_strict_false():
    # End-to-end regression coverage for a fourth latent instance of the
    # same pattern, caught by re-scanning rather than by its own live 400:
    # commands[].values[].value (the nested multi-value variant) has no
    # properties/additionalProperties for its object branch, unlike the
    # sibling top-level commands[].value, which already declares real
    # {on, off} properties and is unaffected.
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_device_send_command.json")
    assert spec.strict is False

    tools = _adapter().export_tools([spec])
    value_schema = (
        tools[0]["function"]["parameters"]["properties"]["commands"]["items"]["properties"]["values"]["items"][
            "properties"
        ]["value"]
    )
    assert "properties" not in value_schema
    assert "additionalProperties" not in value_schema
    assert tools[0]["function"]["strict"] is False


def test_group_scene_op_tool_file_is_strict_false():
    # End-to-end regression coverage for a fifth latent instance: "link"
    # varies per link type ("the member's new link object, in the same
    # exact shape as get_group_detail returns"), so it has no fixed
    # property set either.
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_group_scene_op.json")
    assert spec.strict is False

    tools = _adapter().export_tools([spec])
    link_schema = tools[0]["function"]["parameters"]["properties"]["link"]
    assert "properties" not in link_schema
    assert "additionalProperties" not in link_schema
    assert tools[0]["function"]["strict"] is False


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

"""OpenAIAdapter.export_tools -- strict-mode JSON Schema normalization, and
the per-tool ``"strict": false`` escape hatch for tools whose schema has a
genuinely free-form object parameter (dynamic keys the caller can't
enumerate in advance, e.g. run_diagnostic_step's ``params``).

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


def _fake_completion_response():
    message = SimpleNamespace(content="hi", tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], model_dump=lambda: {})


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


def test_run_diagnostic_step_tool_file_is_strict_false():
    # The exact tool that triggered the live 400 from OpenAI: params is a
    # genuinely free-form object (different keys per diagnostic step), which
    # strict mode cannot represent.
    spec = LLMAdapter.tools_spec_from_file(_TOOLS_DIR / "tool_diagnostics_run_step.json")
    assert spec.strict is False

    tools = _adapter().export_tools([spec])
    params_schema = tools[0]["function"]["parameters"]["properties"]["params"]
    assert params_schema["additionalProperties"] is True
    assert tools[0]["function"]["strict"] is False


def test_all_free_form_params_tool_files_declare_strict_false():
    # Guard against a future tool file reintroducing additionalProperties:
    # true without also opting out of strict mode.
    for name in ("tool_diagnostics_run_step.json", "tool_plan_run_step.json", "tool_plugin_call.json"):
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

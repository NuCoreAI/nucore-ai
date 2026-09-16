"""UnifiedRuntime.handle_query -- what it hands AgenticLoop.run.

Covers session_id threading into llm_config (GrokAdapter reads
config["session_id"] back out, see _extra_headers, to set x-grok-conv-id --
so handle_query must actually put the per-conversation session_id it
already has into the llm_config dict, not just use it for session_store
lookups), and that the system prompt is passed through as the list of
cache sections build_system_prompt_sections returns, not re-joined.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import unified.runtime as runtime_module
from unified.runtime import UnifiedRuntime


class _FakeLoop:
    captured_llm_config: dict | None = None
    captured_system_prompt: object = None

    def __init__(self, **kwargs):
        pass

    async def run(self, *, system_prompt, history_messages, user_message, llm_config):
        _FakeLoop.captured_llm_config = llm_config
        _FakeLoop.captured_system_prompt = system_prompt
        return "ok", []


async def _fake_build_system_prompt_sections(nucore_interface):
    return ["static", "tail"]


@pytest.fixture(autouse=True)
def _patch_collaborators(monkeypatch):
    monkeypatch.setattr(runtime_module, "AgenticLoop", _FakeLoop)
    monkeypatch.setattr(runtime_module, "build_system_prompt_sections", _fake_build_system_prompt_sections)
    _FakeLoop.captured_llm_config = None
    _FakeLoop.captured_system_prompt = None


def _runtime() -> UnifiedRuntime:
    return UnifiedRuntime(
        nucore_interface=SimpleNamespace(),
        llm_client=SimpleNamespace(),
        runtime_config={},
    )


@pytest.mark.asyncio
async def test_handle_query_threads_session_id_into_llm_config():
    await _runtime().handle_query("hi", session_id="conversation-42")
    assert _FakeLoop.captured_llm_config["session_id"] == "conversation-42"


@pytest.mark.asyncio
async def test_handle_query_defaults_session_id_when_not_given():
    await _runtime().handle_query("hi")
    assert _FakeLoop.captured_llm_config["session_id"] == "default"


@pytest.mark.asyncio
async def test_handle_query_passes_system_prompt_sections_through_as_a_list():
    await _runtime().handle_query("hi")
    assert _FakeLoop.captured_system_prompt == ["static", "tail"]

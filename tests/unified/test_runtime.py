"""UnifiedRuntime.handle_query -- session_id threading into llm_config.

Regression coverage for the Grok cache-routing header: GrokAdapter reads
config["session_id"] back out (see _extra_headers) to set x-grok-conv-id, so
handle_query must actually put the per-conversation session_id it already
has into the llm_config dict it hands AgenticLoop.run, not just use it for
session_store lookups.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import unified.runtime as runtime_module
from unified.runtime import UnifiedRuntime


class _FakeLoop:
    captured_llm_config: dict | None = None

    def __init__(self, **kwargs):
        pass

    async def run(self, *, system_prompt, history_messages, user_message, llm_config):
        _FakeLoop.captured_llm_config = llm_config
        return "ok", []


async def _fake_build_system_prompt(nucore_interface):
    return "sys"


@pytest.fixture(autouse=True)
def _patch_collaborators(monkeypatch):
    monkeypatch.setattr(runtime_module, "AgenticLoop", _FakeLoop)
    monkeypatch.setattr(runtime_module, "build_system_prompt", _fake_build_system_prompt)
    _FakeLoop.captured_llm_config = None


@pytest.mark.asyncio
async def test_handle_query_threads_session_id_into_llm_config():
    runtime = UnifiedRuntime(
        nucore_interface=SimpleNamespace(),
        llm_client=SimpleNamespace(),
        runtime_config={},
    )

    await runtime.handle_query("hi", session_id="conversation-42")

    assert _FakeLoop.captured_llm_config["session_id"] == "conversation-42"


@pytest.mark.asyncio
async def test_handle_query_defaults_session_id_when_not_given():
    runtime = UnifiedRuntime(
        nucore_interface=SimpleNamespace(),
        llm_client=SimpleNamespace(),
        runtime_config={},
    )

    await runtime.handle_query("hi")

    assert _FakeLoop.captured_llm_config["session_id"] == "default"

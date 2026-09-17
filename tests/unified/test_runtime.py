"""UnifiedRuntime.handle_query -- what it hands AgenticLoop.run.

Covers session_id threading into llm_config (GrokAdapter reads
config["session_id"] back out, see _extra_headers, to set x-grok-conv-id --
so handle_query must actually put the per-conversation session_id it
already has into the llm_config dict, not just use it for session_store
lookups), and that the system prompt is passed through as the list of
cache sections build_system_prompt_sections returns, not re-joined.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import unified.runtime as runtime_module
from unified.runtime import UnifiedRuntime
from unified.session_store import SessionStore


class _FakeLoop:
    captured_llm_config: dict | None = None
    captured_system_prompt: object = None
    captured_init_kwargs: dict | None = None

    def __init__(self, **kwargs):
        _FakeLoop.captured_init_kwargs = kwargs

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
    _FakeLoop.captured_init_kwargs = None


def _runtime(runtime_config: dict | None = None) -> UnifiedRuntime:
    return UnifiedRuntime(
        nucore_interface=SimpleNamespace(),
        llm_client=SimpleNamespace(),
        runtime_config=runtime_config if runtime_config is not None else {},
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


@pytest.mark.asyncio
async def test_handle_query_threads_fabrication_guard_config_into_agentic_loop():
    await _runtime({"fabrication_guard_mode": "block", "max_fabrication_retries": 3}).handle_query("hi")
    assert _FakeLoop.captured_init_kwargs["fabrication_guard_mode"] == "block"
    assert _FakeLoop.captured_init_kwargs["max_fabrication_retries"] == 3


@pytest.mark.asyncio
async def test_handle_query_defaults_fabrication_guard_config_when_absent():
    await _runtime().handle_query("hi")
    assert _FakeLoop.captured_init_kwargs["fabrication_guard_mode"] == "log"
    assert _FakeLoop.captured_init_kwargs["max_fabrication_retries"] == 1


def test_default_session_store_is_private_per_instance():
    # No session_store given -- each instance gets its own, unshared with
    # any other (the behavior every existing caller/test relies on).
    runtime_a = _runtime()
    runtime_b = _runtime()
    assert runtime_a.session_store is not runtime_b.session_store


def test_explicit_session_store_is_used_as_given():
    shared = SessionStore()
    runtime = UnifiedRuntime(
        nucore_interface=SimpleNamespace(), llm_client=SimpleNamespace(), runtime_config={}, session_store=shared
    )
    assert runtime.session_store is shared


class _RecordingLoop:
    """Records each run() call's history_messages -- used only by the
    concurrency test below, which needs a fake that can tell two overlapping
    calls apart and yield control mid-turn (real AgenticLoop.run does too,
    across its own await points)."""

    calls: list[dict] = []

    def __init__(self, **kwargs):
        pass

    async def run(self, *, system_prompt, history_messages, user_message, llm_config):
        _RecordingLoop.calls.append({"user_message": user_message, "history_messages": list(history_messages)})
        if user_message == "first":
            # Yields control mid-turn, before this call's own
            # history.append() -- if the lock didn't serialize these, the
            # "second" call below would start (and read history) while this
            # one is still "in flight".
            await asyncio.sleep(0.02)
            return "first-response", []
        return "second-response", []


@pytest.mark.asyncio
async def test_concurrent_handle_query_calls_for_same_session_are_serialized(monkeypatch):
    # Reproduces what a caller sharing one SessionStore across connections
    # (run_unified_runtime._run_websocket_server, since the SessionStore fix)
    # needs to be safe: two requests for the *same* session_id, dispatched
    # concurrently, must not race on that session's ConversationHistory.
    monkeypatch.setattr(runtime_module, "AgenticLoop", _RecordingLoop)
    _RecordingLoop.calls = []
    runtime = _runtime()

    await asyncio.gather(
        runtime.handle_query("first", session_id="shared"),
        runtime.handle_query("second", session_id="shared"),
    )

    second_call = next(c for c in _RecordingLoop.calls if c["user_message"] == "second")
    # Proves serialization, not just ordering: "second" only ever starts
    # once "first" has fully finished, including its history.append() --
    # a race would show "second" reading history before "first" landed.
    assert any(m.get("content") == "first" for m in second_call["history_messages"])

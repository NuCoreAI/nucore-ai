"""EisyUIContext must be per-connection state, not a shared/global object --
concurrent websocket connections used to clobber a single module-level
instance's context/message. Also covers user_id (sourced from the context
payload) winning over the per-connection uuid4 fallback as _run_once's
effective session_id, which is what lets identity (and Plan's session
ownership) survive a reconnect.
"""

from __future__ import annotations

import json

import pytest

from unified.models import IntentHandlerResult
from unified.run_unified_runtime import EisyUIContext, _run_once


def test_two_contexts_do_not_share_state():
    a = EisyUIContext()
    b = EisyUIContext()

    a.process_message(json.dumps({"type": "context", "context": {"user_id": "a@example.com"}}))

    assert a.get_user_id() == "a@example.com"
    assert b.get_user_id() is None
    assert b.get_context() is None


def test_user_id_persists_when_a_later_context_omits_it():
    ctx = EisyUIContext()
    ctx.process_message(json.dumps({"type": "context", "context": {"user_id": "a@example.com", "screen": "home"}}))
    ctx.process_message(json.dumps({"type": "context", "context": {"screen": "devices"}}))

    assert ctx.get_user_id() == "a@example.com"  # kept, not cleared
    assert ctx.get_context() == {"screen": "devices"}  # context itself still updates


def test_context_message_returns_none_and_message_returns_stripped_text():
    ctx = EisyUIContext()

    context_result = ctx.process_message(json.dumps({"type": "context", "context": {"user_id": "a@example.com"}}))
    message_result = ctx.process_message(json.dumps({"type": "message", "message": "  turn on the light  "}))

    assert context_result is None
    assert message_result == "turn on the light"


class _FakeRuntime:
    def __init__(self, text: str = "ok"):
        self._text = text
        self.stream_handler = None
        self.calls: list[dict] = []

    async def handle_query(self, query, *, framework_context=None, session_id=None):
        self.calls.append({"query": query, "framework_context": framework_context, "session_id": session_id})
        return IntentHandlerResult(intent="unified", output={"text": self._text})


@pytest.mark.asyncio
async def test_run_once_prefers_user_id_over_the_fallback_session_id():
    runtime = _FakeRuntime()
    ctx = EisyUIContext()
    ctx.process_message(json.dumps({"type": "context", "context": {"user_id": "a@example.com"}}))

    await _run_once(runtime, "hello", ctx, session_id="fallback-uuid")

    assert runtime.calls[0]["session_id"] == "a@example.com"


@pytest.mark.asyncio
async def test_run_once_falls_back_to_session_id_when_no_user_id_seen():
    runtime = _FakeRuntime()
    ctx = EisyUIContext()

    await _run_once(runtime, "hello", ctx, session_id="fallback-uuid")

    assert runtime.calls[0]["session_id"] == "fallback-uuid"


@pytest.mark.asyncio
async def test_run_once_falls_back_to_default_when_neither_is_available():
    runtime = _FakeRuntime()
    ctx = EisyUIContext()

    await _run_once(runtime, "hello", ctx)

    assert runtime.calls[0]["session_id"] == "default"


@pytest.mark.asyncio
async def test_run_once_does_not_dispatch_a_context_only_message():
    runtime = _FakeRuntime()
    ctx = EisyUIContext()

    await _run_once(
        runtime, json.dumps({"type": "context", "context": {"user_id": "a@example.com"}}), ctx, session_id="s1"
    )

    assert runtime.calls == []  # context alone never reaches handle_query

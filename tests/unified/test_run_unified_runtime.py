"""EisyUIContext -- per-connection isolation (regression test for the
verified bug where a single process-wide global instance let one websocket
connection's context/user_id clobber another's) and _run_once's
effective_session_id resolution (durable user_id over the per-connection
uuid4 fallback).
"""

from __future__ import annotations

import json

import pytest

from unified.run_unified_runtime import EisyUIContext, _run_once


def test_two_contexts_stay_isolated_from_each_other():
    ctx_a = EisyUIContext()
    ctx_b = EisyUIContext()

    ctx_a.process_message(json.dumps({"type": "context", "context": {"user_id": "alice@example.com"}}))
    ctx_b.process_message(json.dumps({"type": "context", "context": {"user_id": "bob@example.com"}}))

    assert ctx_a.get_user_id() == "alice@example.com"
    assert ctx_b.get_user_id() == "bob@example.com"
    assert ctx_a.get_context() == {"user_id": "alice@example.com"}
    assert ctx_b.get_context() == {"user_id": "bob@example.com"}


def test_user_id_persists_across_a_later_context_message_that_omits_it():
    ctx = EisyUIContext()
    ctx.process_message(json.dumps({"type": "context", "context": {"user_id": "alice@example.com"}}))
    ctx.process_message(json.dumps({"type": "context", "context": {"some_other_field": 1}}))

    assert ctx.get_user_id() == "alice@example.com"


def test_message_type_returns_the_stripped_message_and_leaves_context_alone():
    ctx = EisyUIContext()
    ctx.process_message(json.dumps({"type": "context", "context": {"user_id": "alice@example.com"}}))

    result = ctx.process_message(json.dumps({"type": "message", "message": "  hello  "}))

    assert result == "hello"
    assert ctx.get_user_id() == "alice@example.com"


class _FakeRuntime:
    def __init__(self):
        self.calls: list[dict] = []
        self.stream_handler = None

    async def handle_query(self, query, framework_context=None, session_id=None):
        self.calls.append({"query": query, "framework_context": framework_context, "session_id": session_id})
        return None


@pytest.mark.asyncio
async def test_run_once_uses_user_id_as_effective_session_id_once_known():
    runtime = _FakeRuntime()
    ctx = EisyUIContext()
    await _run_once(runtime, json.dumps({"type": "context", "context": {"user_id": "alice@example.com"}}), ctx, session_id="fallback-uuid")
    await _run_once(runtime, "what's my status?", ctx, session_id="fallback-uuid")

    assert runtime.calls == [
        {"query": "what's my status?", "framework_context": {"user_id": "alice@example.com"}, "session_id": "alice@example.com"}
    ]


@pytest.mark.asyncio
async def test_run_once_falls_back_to_the_connection_uuid_when_no_user_id_ever_arrives():
    runtime = _FakeRuntime()
    ctx = EisyUIContext()
    await _run_once(runtime, "what's my status?", ctx, session_id="fallback-uuid")

    assert runtime.calls == [
        {"query": "what's my status?", "framework_context": None, "session_id": "fallback-uuid"}
    ]


@pytest.mark.asyncio
async def test_run_once_does_not_dispatch_a_bare_context_only_message():
    runtime = _FakeRuntime()
    ctx = EisyUIContext()
    await _run_once(runtime, json.dumps({"type": "context", "context": {"user_id": "alice@example.com"}}), ctx, session_id="fallback-uuid")

    assert runtime.calls == []

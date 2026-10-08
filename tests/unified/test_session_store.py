"""``SessionStore.lock`` -- the per-session lock that makes it safe to share
one ``SessionStore`` across connections (see ``UnifiedRuntime.handle_query``
and ``run_unified_runtime._run_websocket_server``, which now both do).
"""

from __future__ import annotations

import asyncio

from unified.session_store import SessionStore


def test_lock_returns_the_same_object_for_the_same_session_id():
    store = SessionStore()

    first = store.lock("session-a")
    second = store.lock("session-a")

    assert first is second


def test_lock_returns_different_objects_for_different_session_ids():
    store = SessionStore()

    assert store.lock("session-a") is not store.lock("session-b")


async def test_lock_actually_serializes_concurrent_holders():
    store = SessionStore()
    order: list[str] = []

    async def hold(name: str, sleep: float) -> None:
        async with store.lock("shared"):
            order.append(f"{name}:start")
            await asyncio.sleep(sleep)
            order.append(f"{name}:end")

    # "first" sleeps while holding the lock -- if lock() didn't return the
    # same object (or didn't serialize), "second" would interleave.
    await asyncio.gather(hold("first", 0.02), hold("second", 0))

    assert order == ["first:start", "first:end", "second:start", "second:end"]


def test_get_ui_context_returns_none_for_an_unknown_identity():
    store = SessionStore()

    assert store.get_ui_context("client-1::a@example.com") is None


def test_set_then_get_ui_context_round_trips():
    store = SessionStore()

    store.set_ui_context("client-1::a@example.com", {"screen": "devices"})

    assert store.get_ui_context("client-1::a@example.com") == {"screen": "devices"}


def test_ui_context_is_isolated_per_identity():
    store = SessionStore()

    store.set_ui_context("client-1::a@example.com", {"screen": "devices"})
    store.set_ui_context("client-2::a@example.com", {"screen": "routines"})

    assert store.get_ui_context("client-1::a@example.com") == {"screen": "devices"}
    assert store.get_ui_context("client-2::a@example.com") == {"screen": "routines"}


def test_ui_context_is_not_keyed_by_tool_set():
    """Deliberately not nested under a f"{identity}::{tool_set}" key the way
    conversation history (_sessions) is -- the customer's screen doesn't
    change just because the chatbot switched tool sets (see
    merged-toolsets.md), so one slot per identity is shared across both."""
    store = SessionStore()

    store.set_ui_context("client-1::a@example.com", {"screen": "devices"})

    assert store.get_ui_context("client-1::a@example.com::unified") is None
    assert store.get_ui_context("client-1::a@example.com") == {"screen": "devices"}

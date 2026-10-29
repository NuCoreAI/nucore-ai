"""``SessionStore.lock`` -- the per-session lock that makes it safe to share
one ``SessionStore`` across connections (see ``UnifiedRuntime.handle_query``
and ``run_unified_runtime._run_websocket_server``, which now both do).

Also covers the optional disk-persistence layer (``persist_dir``):
multi-session management per ``(user_id, tool_set)`` (start/list/load/
rename/delete), write-through ``persist``/``get_or_load``, and the
no-op guarantee for every non-durable or persistence-off call -- the
default, zero-behavior-change path every existing caller still gets.
"""

from __future__ import annotations

import asyncio

import pytest

from unified.models import ConversationHistory, ConversationTurn
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


# --- composite_key / no-op guarantees -------------------------------------


def test_composite_key_with_no_session_num_matches_todays_shape():
    assert SessionStore.composite_key("a@example.com", "unified") == "a@example.com::unified"
    assert SessionStore.composite_key("a@example.com", "unified", None) == "a@example.com::unified"


def test_composite_key_with_a_session_num_is_distinct():
    assert SessionStore.composite_key("a@example.com", "unified", 2) == "a@example.com::unified::s2"


async def test_resolve_current_session_id_is_none_when_not_durable():
    store = SessionStore(persist_dir="/should/never/be/touched")
    assert await store.resolve_current_session_id("a@example.com", "unified", durable=False) is None


async def test_non_durable_get_or_load_and_persist_never_touch_disk(tmp_path, monkeypatch):
    store = SessionStore(persist_dir=tmp_path)

    def _boom(*args, **kwargs):
        raise AssertionError("asyncio.to_thread should never be called for a non-durable session")

    monkeypatch.setattr("unified.session_store.asyncio.to_thread", _boom)

    history = await store.get_or_load("a@example.com", "unified", None, durable=False)
    history.append("hi", "yo")
    await store.persist("a@example.com", "unified", None, durable=False)

    assert list(tmp_path.iterdir()) == []


async def test_durable_without_persist_dir_never_touches_disk(monkeypatch):
    store = SessionStore()  # no persist_dir at all

    def _boom(*args, **kwargs):
        raise AssertionError("asyncio.to_thread should never be called with no persist_dir")

    monkeypatch.setattr("unified.session_store.asyncio.to_thread", _boom)

    session_num = await store.resolve_current_session_id("a@example.com", "unified", durable=True)
    assert session_num == 1  # stable in-memory-only sentinel

    history = await store.get_or_load("a@example.com", "unified", session_num, durable=True)
    history.append("hi", "yo")
    await store.persist("a@example.com", "unified", session_num, durable=True)  # must not raise


# --- durable, persisted multi-session management --------------------------


async def test_resolve_current_session_id_bootstraps_session_one_on_first_use(tmp_path):
    store = SessionStore(persist_dir=tmp_path)

    first = await store.resolve_current_session_id("a@example.com", "unified", durable=True)
    second = await store.resolve_current_session_id("a@example.com", "unified", durable=True)

    assert first == 1
    assert second == 1  # cached, stable


async def test_get_or_load_and_persist_round_trip_through_a_real_file(tmp_path):
    store = SessionStore(persist_dir=tmp_path)
    session_num = await store.resolve_current_session_id("a@example.com", "unified", durable=True)

    history = await store.get_or_load("a@example.com", "unified", session_num, durable=True)
    history.append("hi", "yo there")
    await store.persist("a@example.com", "unified", session_num, durable=True)

    # A second, independent store at the same directory must see it.
    fresh_store = SessionStore(persist_dir=tmp_path)
    fresh_session_num = await fresh_store.resolve_current_session_id("a@example.com", "unified", durable=True)
    loaded = await fresh_store.get_or_load("a@example.com", "unified", fresh_session_num, durable=True)

    assert [t.query for t in loaded.turns] == ["hi"]
    assert loaded.turns[0].response == "yo there"


async def test_start_new_session_increments_id_and_switches_current(tmp_path):
    store = SessionStore(persist_dir=tmp_path)
    await store.resolve_current_session_id("a@example.com", "unified", durable=True)  # bootstraps session 1

    previous, new_entry = await store.start_new_session("a@example.com", "unified")

    assert previous["id"] == 1
    assert new_entry["id"] == 2
    assert await store.resolve_current_session_id("a@example.com", "unified", durable=True) == 2


async def test_load_session_switches_the_pointer_and_rejects_an_unknown_id(tmp_path):
    store = SessionStore(persist_dir=tmp_path)
    await store.resolve_current_session_id("a@example.com", "unified", durable=True)
    await store.start_new_session("a@example.com", "unified")  # now on session 2

    entry = await store.load_session("a@example.com", "unified", 1)
    assert entry["id"] == 1
    assert await store.resolve_current_session_id("a@example.com", "unified", durable=True) == 1

    assert await store.load_session("a@example.com", "unified", 999) is None
    # A rejected load leaves the pointer unchanged.
    assert await store.resolve_current_session_id("a@example.com", "unified", durable=True) == 1


async def test_rename_session_updates_the_name_and_rejects_an_unknown_id(tmp_path):
    store = SessionStore(persist_dir=tmp_path)
    await store.resolve_current_session_id("a@example.com", "unified", durable=True)

    assert await store.rename_session("a@example.com", "unified", 1, "Kitchen lighting") is True
    entries = await store.list_sessions("a@example.com", "unified")
    assert entries[0]["name"] == "Kitchen lighting"

    assert await store.rename_session("a@example.com", "unified", 999, "x") is False


async def test_delete_session_removes_a_non_current_session_cleanly(tmp_path):
    store = SessionStore(persist_dir=tmp_path)
    await store.resolve_current_session_id("a@example.com", "unified", durable=True)
    await store.start_new_session("a@example.com", "unified")  # current is now 2

    result = await store.delete_session("a@example.com", "unified", 1)

    deleted, replacement = result
    assert deleted["id"] == 1
    assert replacement is None  # 1 wasn't current -- nothing auto-created
    assert await store.resolve_current_session_id("a@example.com", "unified", durable=True) == 2
    entries = await store.list_sessions("a@example.com", "unified")
    assert [e["id"] for e in entries] == [2]


async def test_delete_session_auto_creates_a_replacement_when_deleting_the_current_one(tmp_path):
    store = SessionStore(persist_dir=tmp_path)
    await store.resolve_current_session_id("a@example.com", "unified", durable=True)

    deleted, replacement = await store.delete_session("a@example.com", "unified", 1)

    assert deleted["id"] == 1
    assert replacement is not None  # a user always has at least one live session
    assert await store.resolve_current_session_id("a@example.com", "unified", durable=True) == replacement["id"]


async def test_delete_session_rejects_an_unknown_id(tmp_path):
    store = SessionStore(persist_dir=tmp_path)
    await store.resolve_current_session_id("a@example.com", "unified", durable=True)

    assert await store.delete_session("a@example.com", "unified", 999) is None


async def test_persist_updates_the_index_turn_count_and_updated_at(tmp_path):
    store = SessionStore(persist_dir=tmp_path)
    session_num = await store.resolve_current_session_id("a@example.com", "unified", durable=True)
    history = await store.get_or_load("a@example.com", "unified", session_num, durable=True)
    history.append("hi", "yo")
    history.append("hi again", "yo again")

    await store.persist("a@example.com", "unified", session_num, durable=True)

    entries = await store.list_sessions("a@example.com", "unified")
    assert entries[0]["turn_count"] == 2


async def test_persist_write_failure_is_swallowed_not_raised(tmp_path):
    # Point persist_dir at a path that's actually a *file* -- any write
    # underneath it must fail (not a directory), and persist() must
    # swallow that rather than propagate it.
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    store = SessionStore(persist_dir=blocker)
    session_num = await store.resolve_current_session_id("a@example.com", "unified", durable=True)
    history = await store.get_or_load("a@example.com", "unified", session_num, durable=True)
    history.append("hi", "yo")

    await store.persist("a@example.com", "unified", session_num, durable=True)  # must not raise


async def test_two_concurrent_session_management_calls_leave_a_valid_index(tmp_path):
    store = SessionStore(persist_dir=tmp_path)
    await store.resolve_current_session_id("a@example.com", "unified", durable=True)

    await asyncio.gather(
        store.start_new_session("a@example.com", "unified"),
        store.rename_session("a@example.com", "unified", 1, "renamed"),
    )

    entries = await store.list_sessions("a@example.com", "unified")
    assert len(entries) == 2  # both operations landed, nothing corrupted


async def test_get_or_load_prefers_an_already_cached_in_memory_entry(tmp_path):
    store = SessionStore(persist_dir=tmp_path)
    session_num = await store.resolve_current_session_id("a@example.com", "unified", durable=True)
    cached = await store.get_or_load("a@example.com", "unified", session_num, durable=True)
    cached.append("already in memory", "yo")

    # Even though nothing was persisted yet, a second get_or_load for the
    # same key must return the *same* in-memory object, not a fresh load.
    again = await store.get_or_load("a@example.com", "unified", session_num, durable=True)
    assert again is cached


# --- formatting -------------------------------------------------------------


def test_llm_facing_formatter_output_is_unchanged_after_the_render_turns_refactor():
    history = ConversationHistory(turns=[ConversationTurn(query="hi", response="yo")])
    store = SessionStore()
    key = store.composite_key("a@example.com", "unified")
    store._sessions[key] = history

    rendered = store.format_history_for_prompt(key)

    assert "**NEVER** use conversation history" in rendered
    assert "## Turn 1:" in rendered
    assert "- **User**: hi" in rendered
    assert "- **Assistant**: yo" in rendered


def test_format_history_for_display_has_no_admonition_line():
    history = ConversationHistory(turns=[ConversationTurn(query="hi", response="yo")])

    rendered = SessionStore.format_history_for_display(history, mode_label="customer")

    assert "NEVER" not in rendered
    assert "customer mode" in rendered
    assert "- **User**: hi" in rendered


def test_format_history_for_display_is_empty_for_no_turns():
    assert SessionStore.format_history_for_display(ConversationHistory(), mode_label="customer") == ""
    assert SessionStore.format_history_for_display(None, mode_label="customer") == ""


def test_format_sessions_table_marks_the_current_entry():
    entries = [
        {"id": 1, "name": "Session 1", "turn_count": 3, "updated_at": "2026-01-01T00:00:00+00:00"},
        {"id": 2, "name": "Session 2", "turn_count": 0, "updated_at": "2026-01-02T00:00:00+00:00"},
    ]

    table = SessionStore.format_sessions_table(entries, current_id=1)

    assert "id" in table and "name" in table and "description" in table
    lines = table.splitlines()
    assert lines[0] == "id | name      | description"
    assert all(set(line) <= {"-", "+"} for line in lines[1::2])
    assert "3 turn(s)" in table and "(current)" in table
    assert "Session 2" in table
    current_row = next(line for line in lines if line.startswith("1 "))
    other_row = next(line for line in lines if line.startswith("2 "))
    assert "(current)" in current_row
    assert "(current)" not in other_row


def test_format_sessions_table_handles_no_sessions():
    table = SessionStore.format_sessions_table([], current_id=None)

    assert table.splitlines()[0] == "id | name | description"

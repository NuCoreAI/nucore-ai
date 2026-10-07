"""EisyUIContext must be per-connection state, not a shared/global object --
concurrent websocket connections used to clobber a single module-level
instance's context/message. Also covers user_id (sourced from the context
payload) winning over the per-connection uuid4 fallback as _run_once's
effective session_id, which is what lets identity (and therefore
conversation history) survive a reconnect.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from unified.models import IntentHandlerResult
from unified.plugin_authoring.handlers import discovery
from unified.run_unified_runtime import EisyUIContext, _resolve_tool_set, _run_once


def test_two_contexts_do_not_share_state():
    a = EisyUIContext()
    b = EisyUIContext()

    a.process_message(json.dumps({"type": "context", "context": {"user": {"username": "a@example.com"}}}))

    assert a.get_user_id() == "a@example.com"
    assert b.get_user_id() is None
    assert b.get_context() is None
    assert b.get_is_developer() is None
    assert b.get_client_id() is None


def test_user_id_persists_when_a_later_context_omits_it():
    ctx = EisyUIContext()
    ctx.process_message(
        json.dumps({"type": "context", "context": {"user": {"username": "a@example.com"}, "screen": "home"}})
    )
    ctx.process_message(json.dumps({"type": "context", "context": {"screen": "devices"}}))

    assert ctx.get_user_id() == "a@example.com"  # kept, not cleared
    assert ctx.get_context() == {"screen": "devices"}  # context itself still updates


def test_context_message_returns_none_and_message_returns_stripped_text():
    ctx = EisyUIContext()

    context_result = ctx.process_message(
        json.dumps({"type": "context", "context": {"user": {"username": "a@example.com"}}})
    )
    message_result = ctx.process_message(json.dumps({"type": "message", "message": "  turn on the light  "}))

    assert context_result is None
    assert message_result == "turn on the light"


def test_is_developer_read_from_nested_user_object():
    ctx = EisyUIContext()
    ctx.process_message(
        json.dumps({"type": "context", "context": {"user": {"username": "a@example.com", "isDeveloper": True}}})
    )

    assert ctx.get_is_developer() is True


def test_is_developer_false_is_not_discarded_in_favor_of_a_stale_true():
    # Regression: isDeveloper is a real boolean, so a later False must win
    # over a prior True -- a truthy `or` fallback would wrongly keep True.
    ctx = EisyUIContext()
    ctx.process_message(
        json.dumps({"type": "context", "context": {"user": {"username": "a@example.com", "isDeveloper": True}}})
    )
    ctx.process_message(
        json.dumps({"type": "context", "context": {"user": {"username": "a@example.com", "isDeveloper": False}}})
    )

    assert ctx.get_is_developer() is False


def test_client_id_persists_when_a_later_context_omits_it():
    ctx = EisyUIContext()
    ctx.process_message(json.dumps({"type": "context", "context": {"clientId": "c-1"}}))
    ctx.process_message(json.dumps({"type": "context", "context": {"screen": "devices"}}))

    assert ctx.get_client_id() == "c-1"  # kept, not cleared


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
    ctx.process_message(json.dumps({"type": "context", "context": {"user": {"username": "a@example.com"}}}))

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
        runtime,
        json.dumps({"type": "context", "context": {"user": {"username": "a@example.com"}}}),
        ctx,
        session_id="s1",
    )

    assert runtime.calls == []  # context alone never reaches handle_query


# --- _resolve_tool_set: tool-set selection, startup refusal, dispatch factory ---


def test_resolve_tool_set_customer_returns_all_none():
    assert _resolve_tool_set("customer", SimpleNamespace()) == (None, None, None)


def test_resolve_tool_set_plugin_authoring_requires_an_output_root():
    with pytest.raises(ValueError):
        _resolve_tool_set("plugin_authoring", SimpleNamespace(), plugin_output_root=None)


def test_resolve_tool_set_plugin_authoring_returns_a_dispatch_factory_not_a_dispatch():
    tool_spec_paths, dispatch_factory, system_prompt_builder = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root="/tmp/plugin-projects"
    )

    assert tool_spec_paths  # non-empty: this package's own tools + reused customer ones
    assert callable(system_prompt_builder)
    assert callable(dispatch_factory)

    # Calling the factory twice must each time hand back a
    # _PluginAuthoringDispatch bundle, each backed by its own fresh
    # EvidenceLedger (see the cross-connection-isolation test below) --
    # .dispatch is what's actually usable as a dispatch callable;
    # .extra_tools/.on_raw_response are the Claude-native web search path's
    # own additions (None here, no provider given).
    first_bundle = dispatch_factory()
    second_bundle = dispatch_factory()
    assert callable(first_bundle.dispatch)
    assert callable(second_bundle.dispatch)
    assert first_bundle.extra_tools is None
    assert first_bundle.on_raw_response is None


@pytest.mark.asyncio
async def test_resolve_tool_set_plugin_authoring_dispatch_routes_a_known_tool():
    _, dispatch_factory, _ = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root="/tmp/plugin-projects"
    )
    dispatch = dispatch_factory().dispatch

    result = await dispatch("lookup_uom", {"keyword": "amps"})

    assert any(m["id"] == "1" for m in result["matches"])


def test_make_dispatch_still_works_when_called_with_no_eisy_ui_context():
    # Zero-arg calls (every pre-existing test above, and any caller that
    # hasn't been updated to pass one) must keep working.
    _, dispatch_factory, _ = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root="/tmp/plugin-projects"
    )
    bundle = dispatch_factory()
    assert callable(bundle.dispatch)


@pytest.mark.asyncio
async def test_make_dispatch_binds_live_eisy_ui_context_get_user_id(tmp_path):
    # The bound get_user_id must reflect this EisyUIContext's *current*
    # user_id at call time, not whatever it was when the factory ran (which
    # is always None, since _make_dispatch always runs before any
    # context message could have arrived) -- a snapshot would make
    # configure_developer's identity check permanently useless.
    ctx = EisyUIContext()
    _, dispatch_factory, _ = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root=str(tmp_path)
    )
    dispatch = dispatch_factory(ctx).dispatch

    # No identity known yet -- commissioning under a different email is allowed.
    first = await dispatch("configure_developer", {"email": "dev@example.com", "name": "Dev"})
    assert first["configured"] is True

    # Mutate the same ctx instance after the dispatch bundle was built.
    ctx.process_message(
        json.dumps({"type": "context", "context": {"user": {"username": "someone-else@example.com"}}})
    )

    second = await dispatch("configure_developer", {"email": "dev@example.com", "name": "Dev"})
    assert "error" in second


# --- _resolve_tool_set: search_web registration (design/developers/impl_plan.md Phase 2) ---


def _tool_names(tool_spec_paths):
    return {p.name for p in tool_spec_paths}


def test_resolve_tool_set_excludes_search_web_without_an_engine_or_key(monkeypatch):
    monkeypatch.delenv("SEARCH_ENGINE_API_KEY", raising=False)
    tool_spec_paths, _, _ = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root="/tmp/x", search_engine=None
    )
    assert "tool_search_web.json" not in _tool_names(tool_spec_paths)


def test_resolve_tool_set_excludes_search_web_when_engine_given_but_key_missing(monkeypatch):
    monkeypatch.delenv("SEARCH_ENGINE_API_KEY", raising=False)
    tool_spec_paths, _, _ = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root="/tmp/x", search_engine="brave"
    )
    assert "tool_search_web.json" not in _tool_names(tool_spec_paths)


@pytest.mark.parametrize("engine", ["brave", "tavily"])
def test_resolve_tool_set_includes_search_web_when_engine_and_key_both_given(monkeypatch, engine):
    monkeypatch.setenv("SEARCH_ENGINE_API_KEY", "a-key")
    tool_spec_paths, _, _ = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root="/tmp/x", search_engine=engine
    )
    assert "tool_search_web.json" in _tool_names(tool_spec_paths)


@pytest.mark.asyncio
async def test_resolve_tool_set_factory_gives_each_connection_an_independent_ledger(monkeypatch):
    """Proves no cross-connection evidence leakage: exhausting one
    connection's search_github_plugins query cap must not affect a second,
    separately-constructed connection's own (independent) cap/counter. The
    GitHub call itself is mocked (same pattern as test_discovery.py); only
    the per-ledger counter matters here."""
    monkeypatch.setenv("SEARCH_ENGINE_API_KEY", "a-key")

    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"items": []}
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: cm)

    _, dispatch_factory, _ = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root="/tmp/x", search_engine="brave"
    )
    first_connection = dispatch_factory().dispatch
    second_connection = dispatch_factory().dispatch

    for _ in range(discovery.MAX_GITHUB_QUERIES):
        result = await first_connection("search_github_plugins", {"query": "pool controller"})
        assert "error" not in result
    over_cap = await first_connection("search_github_plugins", {"query": "pool controller"})
    assert "error" in over_cap  # first connection's own cap reached

    fresh = await second_connection("search_github_plugins", {"query": "pool controller"})
    assert "error" not in fresh  # second connection's independent ledger/counter, unaffected by the first's cap


# --- _resolve_tool_set: native Claude web search vs Brave/Tavily fallback ---


def test_resolve_tool_set_uses_native_claude_search_when_provider_is_claude_and_no_engine_given(monkeypatch):
    monkeypatch.delenv("SEARCH_ENGINE_API_KEY", raising=False)
    tool_spec_paths, dispatch_factory, _ = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root="/tmp/x", search_engine=None, provider="claude"
    )
    assert "tool_search_web.json" not in _tool_names(tool_spec_paths)

    bundle = dispatch_factory()
    assert bundle.extra_tools == [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": discovery.MAX_WEB_SEARCH_QUERIES}
    ]
    assert callable(bundle.on_raw_response)


def test_resolve_tool_set_normalizes_the_anthropic_alias_to_claude(monkeypatch):
    monkeypatch.delenv("SEARCH_ENGINE_API_KEY", raising=False)
    _, dispatch_factory, _ = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root="/tmp/x", search_engine=None, provider="anthropic"
    )
    assert dispatch_factory().extra_tools is not None


@pytest.mark.parametrize("engine", ["brave", "tavily"])
def test_resolve_tool_set_explicit_search_engine_wins_over_native_even_on_claude(monkeypatch, engine):
    monkeypatch.setenv("SEARCH_ENGINE_API_KEY", "a-key")
    tool_spec_paths, dispatch_factory, _ = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root="/tmp/x", search_engine=engine, provider="claude"
    )
    assert "tool_search_web.json" in _tool_names(tool_spec_paths)
    bundle = dispatch_factory()
    assert bundle.extra_tools is None
    assert bundle.on_raw_response is None


def test_resolve_tool_set_non_claude_provider_without_engine_has_no_web_search_at_all(monkeypatch):
    monkeypatch.delenv("SEARCH_ENGINE_API_KEY", raising=False)
    tool_spec_paths, dispatch_factory, _ = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root="/tmp/x", search_engine=None, provider="openai"
    )
    assert "tool_search_web.json" not in _tool_names(tool_spec_paths)
    bundle = dispatch_factory()
    assert bundle.extra_tools is None
    assert bundle.on_raw_response is None


@pytest.mark.asyncio
async def test_native_claude_search_marks_web_search_available_without_an_engine_configured(monkeypatch):
    # EvidenceLedger.web_search_available must reflect native-or-fallback,
    # not just the Brave/Tavily fallback -- proven here via the still-live
    # fetch_reference(source_tier="user_url") gate, which refuses until
    # web_search_tried (never set yet) when web_search_available is True.
    monkeypatch.delenv("SEARCH_ENGINE_API_KEY", raising=False)
    _, dispatch_factory, _ = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root="/tmp/x", search_engine=None, provider="claude"
    )
    dispatch = dispatch_factory().dispatch

    result = await dispatch("fetch_reference", {"url": "https://example.com/docs", "source_tier": "user_url"})
    assert "error" in result


@pytest.mark.asyncio
async def test_no_native_and_no_fallback_leaves_web_search_unavailable(monkeypatch):
    monkeypatch.delenv("SEARCH_ENGINE_API_KEY", raising=False)

    response = MagicMock()
    response.status_code = 200
    response.text = "docs"
    response.headers = {"content-type": "text/plain"}
    client = AsyncMock()
    client.get = AsyncMock(return_value=response)
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=client)
    cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: cm)

    _, dispatch_factory, _ = _resolve_tool_set(
        "plugin_authoring", SimpleNamespace(), plugin_output_root="/tmp/x", search_engine=None, provider="openai"
    )
    dispatch = dispatch_factory().dispatch

    result = await dispatch("fetch_reference", {"url": "https://example.com/docs", "source_tier": "user_url"})
    # Not rejected by the ask-for-urls gate -- web_search_available is False
    # (no native, no fallback engine), so can_ask_for_urls() is True
    # immediately and the mocked fetch itself succeeds.
    assert "error" not in result

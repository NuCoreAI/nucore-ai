"""EisyUIContext must be per-connection state, not a shared/global object --
concurrent websocket connections used to clobber a single module-level
instance's context/message. Also covers the combined client_id+user_id
identity (sourced from the context payload) winning over the per-connection
uuid4 fallback as _run_once's effective session_id -- combined, not user_id
alone, so the same logged-in user_id on a different browser/machine lands
in a different session -- and, via a shared SessionStore, how UI context
itself survives a reconnect (a brand-new, empty EisyUIContext) even though
the object holding it does not.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from unified.models import IntentHandlerResult
from unified.plugin_authoring.handlers import discovery
from unified.session_store import SessionStore
from unified import run_unified_runtime
from unified.run_unified_runtime import (
    EisyUIContext,
    _build_parser,
    _build_plugin_authoring_tool_set,
    _run_once,
    main,
)


def _resolve_plugin_authoring(**kwargs):
    """Test helper matching the old `_resolve_tool_set("plugin_authoring", ...)`
    call shape, now that the dispatcher function is gone -- there is only
    one tool set `_build_plugin_authoring_tool_set` ever builds, since
    "customer"/"unified" needs no explicit resolution at all (UnifiedRuntime
    falls back to its own hardcoded default -- see merged-toolsets.md)."""
    import os

    kwargs.setdefault("nucore_interface", SimpleNamespace())
    kwargs.setdefault("plugin_output_root", "/tmp/plugin-projects")
    kwargs.setdefault("search_engine_api_key", os.environ.get("SEARCH_ENGINE_API_KEY"))
    bundle = _build_plugin_authoring_tool_set(**kwargs)
    return bundle.tool_spec_paths, bundle.dispatch_factory, bundle.system_prompt_builder


def test_two_contexts_do_not_share_state():
    a = EisyUIContext()
    b = EisyUIContext()

    a.process_message(json.dumps({"type": "context", "context": {"user": {"username": "a@example.com"}}}))

    assert a.get_user_id() == "a@example.com"
    assert b.get_user_id() is None
    assert b.get_context() is None
    assert b.get_is_developer() is None
    assert b.get_client_id() is None


def test_get_identity_key_combines_client_id_and_user_id():
    ctx = EisyUIContext()
    ctx.process_message(
        json.dumps({"type": "context", "context": {"clientId": "client-1", "user": {"username": "a@example.com"}}})
    )

    assert ctx.get_identity_key() == "client-1::a@example.com"


def test_get_identity_key_is_none_with_no_identity_seen_yet():
    ctx = EisyUIContext()

    assert ctx.get_identity_key() is None


def test_get_identity_key_degrades_gracefully_with_only_one_field():
    client_only = EisyUIContext()
    client_only.process_message(json.dumps({"type": "context", "context": {"clientId": "client-1"}}))
    user_only = EisyUIContext()
    user_only.process_message(json.dumps({"type": "context", "context": {"user": {"username": "a@example.com"}}}))

    assert client_only.get_identity_key() == "client-1::-"
    assert user_only.get_identity_key() == "-::a@example.com"


def test_reconnect_recovers_ui_context_from_the_shared_session_store():
    """The scenario the user reported: a websocket reconnect builds a brand
    new, empty EisyUIContext (see that class's own docstring on why it's
    per-connection, not global) -- without a shared SessionStore, that
    looked exactly like the customer's screen had become unknown. With one,
    the new connection's first context message (even a terse one that
    doesn't repeat the screen) still lets get_context() recover the
    previous connection's last known value until this one sends its own."""
    store = SessionStore()
    first_connection = EisyUIContext(session_store=store)
    first_connection.process_message(
        json.dumps(
            {
                "type": "context",
                "context": {"clientId": "client-1", "user": {"username": "a@example.com"}, "screen": "devices"},
            }
        )
    )
    assert first_connection.get_context() == {
        "clientId": "client-1",
        "user": {"username": "a@example.com"},
        "screen": "devices",
    }

    # New connection -- same customer (same identity), reconnecting.
    second_connection = EisyUIContext(session_store=store)
    assert second_connection.get_context() is None  # nothing on *this* connection yet

    second_connection.process_message(
        json.dumps({"type": "context", "context": {"clientId": "client-1", "user": {"username": "a@example.com"}}})
    )

    # This connection's own context message didn't repeat "screen", but its
    # own .context is now set (even without "screen") -- get_context()
    # returns exactly what this connection last said, same as before this
    # feature existed; the read-through only fills the gap *before* that.
    assert second_connection.get_context() == {"clientId": "client-1", "user": {"username": "a@example.com"}}


def test_a_new_connection_before_any_context_message_reads_through_the_shared_store():
    store = SessionStore()
    first_connection = EisyUIContext(session_store=store)
    first_connection.process_message(
        json.dumps(
            {
                "type": "context",
                "context": {"clientId": "client-1", "user": {"username": "a@example.com"}, "screen": "devices"},
            }
        )
    )

    second_connection = EisyUIContext(session_store=store)
    # Simulate this connection already knowing the identity (e.g. carried
    # over by whatever establishes the connection) without having received
    # a context message of its own yet -- get_context() must still recover
    # the shared last-known value rather than report "unknown".
    second_connection.client_id = "client-1"
    second_connection.user_id = "a@example.com"

    assert second_connection.get_context() == {
        "clientId": "client-1",
        "user": {"username": "a@example.com"},
        "screen": "devices",
    }


def test_different_identities_do_not_share_ui_context_through_the_store():
    store = SessionStore()
    ctx_a = EisyUIContext(session_store=store)
    ctx_a.process_message(
        json.dumps(
            {"type": "context", "context": {"clientId": "client-1", "user": {"username": "a@example.com"}, "screen": "devices"}}
        )
    )
    ctx_b = EisyUIContext(session_store=store)
    ctx_b.process_message(
        json.dumps({"type": "context", "context": {"clientId": "client-2", "user": {"username": "b@example.com"}}})
    )

    assert ctx_b.get_context() == {"clientId": "client-2", "user": {"username": "b@example.com"}}


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


def test_developer_switch_command_recognized_from_a_websocket_message():
    ctx = EisyUIContext()

    result = ctx.process_message(json.dumps({"type": "message", "message": "/developer"}))

    assert result is None
    assert ctx.get_active_tool_set() == "plugin_authoring"


def test_customer_switch_command_recognized_from_a_websocket_message():
    ctx = EisyUIContext()
    ctx.process_message(json.dumps({"type": "message", "message": "/developer"}))

    result = ctx.process_message(json.dumps({"type": "message", "message": "/customer"}))

    assert result is None
    assert ctx.get_active_tool_set() == "unified"


def test_developer_switch_command_recognized_from_a_plain_repl_string():
    # Regression: REPL/--query input is never JSON-wrapped (unlike a
    # WebSocket "type": "message" payload), so process_message's plain-
    # string fallback must apply the same switch-command check, or
    # /developer only ever worked over WebSocket.
    ctx = EisyUIContext()

    result = ctx.process_message("/developer")

    assert result is None
    assert ctx.get_active_tool_set() == "plugin_authoring"


def test_non_command_plain_repl_string_passes_through_unchanged():
    ctx = EisyUIContext()

    result = ctx.process_message("  turn on the light  ")

    assert result == "turn on the light"
    assert ctx.get_active_tool_set() is None


# --- CLI surface: --preferences-dir/--prompt-log-file are the only source of
# truth for these (never read from runtime config -- a deployment/host
# concern, not a customer-configurable one; see
# design/developers/merged-toolsets.md) ---


def test_preferences_dir_and_prompt_log_file_flags_parse():
    args = _build_parser().parse_args(
        [
            "--preferences-dir",
            "/etc/nucore/prefs",
            "--prompt-log-file",
            "/var/log/nucore/prompt.jsonl",
            "--plugin-output-root",
            "/etc/nucore/plugins",
        ]
    )

    assert args.preferences_dir == "/etc/nucore/prefs"
    assert args.prompt_log_file == "/var/log/nucore/prompt.jsonl"
    assert args.plugin_output_root == "/etc/nucore/plugins"


def test_preferences_dir_and_prompt_log_file_default_to_none():
    args = _build_parser().parse_args([])

    assert args.preferences_dir is None
    assert args.prompt_log_file is None
    assert args.plugin_output_root is None


def test_removed_cli_flags_are_rejected():
    # Regression guard: --prompt-log-dir/--no-prompt-log were collapsed into
    # --prompt-log-file; --tool-set/--search-engine/--stream/--max-iterations/
    # --json-output/--prompt_type/--query were removed outright in earlier
    # cleanup. --plugin-output-root was removed in that same cleanup and then
    # reinstated later (see design/developers/merged-toolsets.md's
    # "CLI-only, deliberately" section) -- it is deliberately *not* in this
    # list any more; its own parse test lives above. None of the flags below
    # should silently resurrect.
    for removed_flag in (
        "--prompt-log-dir",
        "--no-prompt-log",
        "--tool-set",
        "--query",
    ):
        with pytest.raises(SystemExit):
            _build_parser().parse_args([removed_flag, "x"])


def test_plugin_output_root_required_when_plugin_authoring_enabled(tmp_path, monkeypatch):
    """plugin_output_root is CLI-only (--plugin-output-root) again -- never read
    from runtime config (see design/developers/merged-toolsets.md's "CLI-only,
    deliberately" section). main() itself now enforces the same
    "required when plugin_authoring.enabled" rule runtime_config.py's loader
    used to apply to the now-removed config key of the same name."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(run_unified_runtime, "_load_backend_api", lambda **_kwargs: SimpleNamespace())

    config_path = tmp_path / "runtime_config.json"
    config_path.write_text(
        json.dumps(
            {
                "nucore_runtime": {
                    "unified": {"provider": "claude", "model": "m"},
                    "plugin_authoring": {"provider": "claude", "model": "m"},
                }
            }
        )
    )
    args = _build_parser().parse_args(["--runtime-config", str(config_path)])

    with pytest.raises(ValueError, match="--plugin-output-root"):
        main(args)


def test_main_accepts_an_in_process_dict_in_place_of_a_runtime_config_path(monkeypatch):
    """args.runtime_config is normally a path string -- the only thing argparse
    can ever produce from real argv. An in-process caller invoking main()
    directly (not through a real CLI subprocess) may instead build its own
    args namespace with an already-parsed dict there, skipping the
    "write it to a temp file just to point a path at it" round trip -- see
    _load_runtime_config's docstring. This drives main() through REPL mode
    (no --websocket-port) end to end with that dict, with stdin faked as
    already closed so the REPL loop exits on its very first iteration."""

    def _raise_eof(*_args, **_kwargs):
        raise EOFError

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(run_unified_runtime, "_load_backend_api", lambda **_kwargs: SimpleNamespace())
    monkeypatch.setattr("builtins.input", _raise_eof)

    args = _build_parser().parse_args([])
    args.runtime_config = {"nucore_runtime": {"unified": {"provider": "claude", "model": "m"}}}

    main(args)  # must not raise -- REPL loop exits immediately on EOFError


class _FakeRuntime:
    def __init__(self, text: str = "ok"):
        self._text = text
        self.stream_handler = None
        self.calls: list[dict] = []

    async def handle_query(self, query, *, framework_context=None, session_id=None, **_kwargs):
        self.calls.append({"query": query, "framework_context": framework_context, "session_id": session_id})
        return IntentHandlerResult(intent="unified", output={"text": self._text})


@pytest.mark.asyncio
async def test_run_once_prefers_the_combined_identity_over_the_fallback_session_id():
    runtime = _FakeRuntime()
    ctx = EisyUIContext()
    ctx.process_message(
        json.dumps({"type": "context", "context": {"clientId": "client-1", "user": {"username": "a@example.com"}}})
    )

    await _run_once(runtime, "hello", ctx, session_id="fallback-uuid")

    assert runtime.calls[0]["session_id"] == "client-1::a@example.com"


@pytest.mark.asyncio
async def test_run_once_gives_the_same_user_id_on_a_different_client_a_different_session():
    """Regression guard: session_id used to be user_id alone, so the same
    logged-in customer on two different browsers/machines landed in the
    *same* conversation history/UI context -- wrong, since they're
    different physical sessions. client_id (not just user_id) must be part
    of the identity so they land in different sessions."""
    runtime = _FakeRuntime()
    ctx_a = EisyUIContext()
    ctx_a.process_message(
        json.dumps({"type": "context", "context": {"clientId": "laptop", "user": {"username": "a@example.com"}}})
    )
    ctx_b = EisyUIContext()
    ctx_b.process_message(
        json.dumps({"type": "context", "context": {"clientId": "phone", "user": {"username": "a@example.com"}}})
    )

    await _run_once(runtime, "hello from laptop", ctx_a, session_id="fallback-a")
    await _run_once(runtime, "hello from phone", ctx_b, session_id="fallback-b")

    assert runtime.calls[0]["session_id"] != runtime.calls[1]["session_id"]


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


# --- _build_plugin_authoring_tool_set: dispatch factory shape ---
# (there is no more tool-set dispatcher -- "customer"/"unified" needs no
# resolution at all. 'enabled' validation lives in runtime_config.py -- see
# test_runtime_config.py. plugin_output_root itself is CLI-only
# (--plugin-output-root) and required-when-enabled validation for it lives
# in run_unified_runtime.py's main() -- see
# test_plugin_output_root_required_when_plugin_authoring_enabled below.)


def test_build_plugin_authoring_tool_set_returns_a_dispatch_factory_not_a_dispatch():
    tool_spec_paths, dispatch_factory, system_prompt_builder = _resolve_plugin_authoring()

    assert tool_spec_paths  # non-empty: this package's own tools + reused customer ones
    assert callable(system_prompt_builder)
    assert callable(dispatch_factory)

    # Calling the factory twice must each time hand back a DispatchBundle,
    # each backed by its own fresh EvidenceLedger (see the
    # cross-connection-isolation test below) -- .dispatch is what's actually
    # usable as a dispatch callable; .extra_tools/.on_raw_response are the
    # Claude-native web search path's own additions (None here, no provider
    # given).
    first_bundle = dispatch_factory()
    second_bundle = dispatch_factory()
    assert callable(first_bundle.dispatch)
    assert callable(second_bundle.dispatch)
    assert first_bundle.extra_tools is None
    assert first_bundle.on_raw_response is None


@pytest.mark.asyncio
async def test_build_plugin_authoring_tool_set_dispatch_routes_a_known_tool():
    _, dispatch_factory, _ = _resolve_plugin_authoring()
    dispatch = dispatch_factory().dispatch

    result = await dispatch("lookup_uom", {"keyword": "amps"})

    assert any(m["id"] == "1" for m in result["matches"])


def test_make_dispatch_still_works_when_called_with_no_eisy_ui_context():
    # Zero-arg calls (every pre-existing test above, and any caller that
    # hasn't been updated to pass one) must keep working.
    _, dispatch_factory, _ = _resolve_plugin_authoring()
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
    _, dispatch_factory, _ = _resolve_plugin_authoring(plugin_output_root=str(tmp_path))
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
    tool_spec_paths, _, _ = _resolve_plugin_authoring(
        plugin_output_root="/tmp/x", search_engine=None
    )
    assert "tool_search_web.json" not in _tool_names(tool_spec_paths)


def test_resolve_tool_set_excludes_search_web_when_engine_given_but_key_missing(monkeypatch):
    monkeypatch.delenv("SEARCH_ENGINE_API_KEY", raising=False)
    tool_spec_paths, _, _ = _resolve_plugin_authoring(
        plugin_output_root="/tmp/x", search_engine="brave"
    )
    assert "tool_search_web.json" not in _tool_names(tool_spec_paths)


@pytest.mark.parametrize("engine", ["brave", "tavily"])
def test_resolve_tool_set_includes_search_web_when_engine_and_key_both_given(monkeypatch, engine):
    monkeypatch.setenv("SEARCH_ENGINE_API_KEY", "a-key")
    tool_spec_paths, _, _ = _resolve_plugin_authoring(
        plugin_output_root="/tmp/x", search_engine=engine
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

    _, dispatch_factory, _ = _resolve_plugin_authoring(
        plugin_output_root="/tmp/x", search_engine="brave"
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
    tool_spec_paths, dispatch_factory, _ = _resolve_plugin_authoring(
        plugin_output_root="/tmp/x", search_engine=None, provider="claude"
    )
    assert "tool_search_web.json" not in _tool_names(tool_spec_paths)

    bundle = dispatch_factory()
    assert bundle.extra_tools == [
        {"type": "web_search_20250305", "name": "web_search", "max_uses": discovery.MAX_WEB_SEARCH_QUERIES}
    ]
    assert callable(bundle.on_raw_response)


def test_resolve_tool_set_normalizes_the_anthropic_alias_to_claude(monkeypatch):
    monkeypatch.delenv("SEARCH_ENGINE_API_KEY", raising=False)
    _, dispatch_factory, _ = _resolve_plugin_authoring(
        plugin_output_root="/tmp/x", search_engine=None, provider="anthropic"
    )
    assert dispatch_factory().extra_tools is not None


@pytest.mark.parametrize("engine", ["brave", "tavily"])
def test_resolve_tool_set_explicit_search_engine_wins_over_native_even_on_claude(monkeypatch, engine):
    monkeypatch.setenv("SEARCH_ENGINE_API_KEY", "a-key")
    tool_spec_paths, dispatch_factory, _ = _resolve_plugin_authoring(
        plugin_output_root="/tmp/x", search_engine=engine, provider="claude"
    )
    assert "tool_search_web.json" in _tool_names(tool_spec_paths)
    bundle = dispatch_factory()
    assert bundle.extra_tools is None
    assert bundle.on_raw_response is None


def test_resolve_tool_set_non_claude_provider_without_engine_has_no_web_search_at_all(monkeypatch):
    monkeypatch.delenv("SEARCH_ENGINE_API_KEY", raising=False)
    tool_spec_paths, dispatch_factory, _ = _resolve_plugin_authoring(
        plugin_output_root="/tmp/x", search_engine=None, provider="openai"
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
    _, dispatch_factory, _ = _resolve_plugin_authoring(
        plugin_output_root="/tmp/x", search_engine=None, provider="claude"
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

    _, dispatch_factory, _ = _resolve_plugin_authoring(
        plugin_output_root="/tmp/x", search_engine=None, provider="openai"
    )
    dispatch = dispatch_factory().dispatch

    result = await dispatch("fetch_reference", {"url": "https://example.com/docs", "source_tier": "user_url"})
    # Not rejected by the ask-for-urls gate -- web_search_available is False
    # (no native, no fallback engine), so can_ask_for_urls() is True
    # immediately and the mocked fetch itself succeeds.
    assert "error" not in result

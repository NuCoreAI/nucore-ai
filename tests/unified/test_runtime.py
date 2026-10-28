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
import json
from types import SimpleNamespace

import pytest

import unified.runtime as runtime_module
from unified.runtime import _TOOLS_DIR, DispatchBundle, ToolSetBundle, UnifiedRuntime, resolve_llm_profile
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


def _runtime(runtime_config: dict | None = None, **kwargs) -> UnifiedRuntime:
    return UnifiedRuntime(
        nucore_interface=SimpleNamespace(),
        llm_client=SimpleNamespace(),
        runtime_config=runtime_config if runtime_config is not None else {},
        **kwargs,
    )


@pytest.mark.asyncio
async def test_handle_query_threads_session_id_into_llm_config():
    await _runtime().handle_query("hi", session_id="conversation-42")
    assert _FakeLoop.captured_llm_config["session_id"] == "conversation-42::unified"


@pytest.mark.asyncio
async def test_handle_query_defaults_session_id_when_not_given():
    await _runtime().handle_query("hi")
    assert _FakeLoop.captured_llm_config["session_id"] == "default::unified"


@pytest.mark.asyncio
async def test_handle_query_passes_system_prompt_sections_through_as_a_list():
    await _runtime().handle_query("hi")
    assert _FakeLoop.captured_system_prompt == ["static", "tail"]


@pytest.mark.asyncio
async def test_handle_query_threads_fabrication_guard_config_into_agentic_loop():
    runtime_config = {
        "nucore_runtime": {"unified": {"fabrication_guard_mode": "block", "max_fabrication_retries": 3}}
    }
    await _runtime(runtime_config).handle_query("hi")
    assert _FakeLoop.captured_init_kwargs["fabrication_guard_mode"] == "block"
    assert _FakeLoop.captured_init_kwargs["max_fabrication_retries"] == 3


@pytest.mark.asyncio
async def test_handle_query_defaults_fabrication_guard_config_when_absent():
    await _runtime().handle_query("hi")
    assert _FakeLoop.captured_init_kwargs["fabrication_guard_mode"] == "log"
    assert _FakeLoop.captured_init_kwargs["max_fabrication_retries"] == 1


@pytest.mark.asyncio
async def test_handle_query_falls_back_to_default_tool_set_for_an_unregistered_active_tool_set():
    # Backstop against a stale/invalid active_tool_set from any caller --
    # e.g. a sticky value from before a config reload -- degrading to the
    # default tool set instead of raising KeyError at self._tool_sets[...].
    result = await _runtime().handle_query("hi", active_tool_set="not_a_real_tool_set")
    assert _FakeLoop.captured_llm_config["session_id"] == "default::unified"
    assert result.output["active_tool_set"] == "unified"


def test_is_tool_set_enabled_reflects_enabled_profiles():
    runtime = _runtime({"enabled_profiles": ["unified"]})
    assert runtime.is_tool_set_enabled("unified") is True
    assert runtime.is_tool_set_enabled("plugin_authoring") is False


async def _dispatch_through_delete_confirmation_gate(query: str, name: str, args: dict) -> dict:
    """Runs one handle_query turn (with *query* as the raw customer text),
    then invokes the resulting wrapped dispatch callable directly with
    (name, args) against a recording fake base dispatch, returning exactly
    the args that reached it -- isolates _wrap_dispatch_for_delete_
    confirmation's own rewriting logic from the rest of the dispatch chain."""
    received: list[dict] = []

    async def _fake_base_dispatch(dispatched_name, dispatched_args):
        received.append(dispatched_args)
        return {"ok": True}

    tool_sets = {
        "unified": ToolSetBundle(
            tool_spec_paths=[],
            dispatch_factory=lambda _ctx: DispatchBundle(dispatch=_fake_base_dispatch),
            system_prompt_builder=_fake_build_system_prompt_sections,
        )
    }
    await _runtime(tool_sets=tool_sets).handle_query(query)
    dispatch = _FakeLoop.captured_init_kwargs["dispatch"]
    await dispatch(name, args)
    return received[0]


@pytest.mark.asyncio
async def test_delete_confirmation_gate_passes_through_on_exact_yes_and_model_confirmed():
    args = await _dispatch_through_delete_confirmation_gate(
        "YES", "node_op", {"operation": "delete", "node_id": "n1", "confirmed": True}
    )
    assert args["confirmed"] is True


@pytest.mark.asyncio
async def test_delete_confirmation_gate_trims_only_outer_whitespace():
    args = await _dispatch_through_delete_confirmation_gate(
        "  YES  \n", "node_op", {"operation": "delete", "node_id": "n1", "confirmed": True}
    )
    assert args["confirmed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_reply", ["yes", "Y", "YES please", "YES.", "ye", "YES\nplease"])
async def test_delete_confirmation_gate_rejects_anything_but_exact_yes(bad_reply):
    args = await _dispatch_through_delete_confirmation_gate(
        bad_reply, "node_op", {"operation": "delete", "node_id": "n1", "confirmed": True}
    )
    assert args["confirmed"] is False


@pytest.mark.asyncio
async def test_delete_confirmation_gate_requires_model_confirmed_too():
    # Exact "YES" alone is not sufficient -- the model's own confirmed must
    # also be true (AND, not a replacement for the model's own judgment).
    args = await _dispatch_through_delete_confirmation_gate(
        "YES", "node_op", {"operation": "delete", "node_id": "n1"}
    )
    assert args["confirmed"] is False


@pytest.mark.asyncio
async def test_delete_confirmation_gate_ignores_non_delete_operations():
    args = await _dispatch_through_delete_confirmation_gate(
        "not yes", "node_op", {"operation": "rename", "node_id": "n1", "new_name": "x"}
    )
    assert args == {"operation": "rename", "node_id": "n1", "new_name": "x"}


@pytest.mark.asyncio
async def test_delete_confirmation_gate_ignores_tool_names_outside_the_gated_set():
    args = await _dispatch_through_delete_confirmation_gate("not yes", "some_other_tool", {"foo": "bar"})
    assert args == {"foo": "bar"}


@pytest.mark.asyncio
@pytest.mark.parametrize("gated_tool", ["delete_plugin", "delete_registered_plugin"])
async def test_delete_confirmation_gate_covers_single_purpose_delete_tools(gated_tool):
    args = await _dispatch_through_delete_confirmation_gate(
        "not yes", gated_tool, {"location": "acme_pool", "confirmed": True}
    )
    assert args["confirmed"] is False


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


# --- Alternate tool set injection (unified.plugin_authoring uses ToolSetBundle) ---


@pytest.mark.asyncio
async def test_default_tool_spec_paths_matches_hardcoded_customer_tools_dir():
    expected_count = len(list(_TOOLS_DIR.glob("tool_*.json")))
    await _runtime().handle_query("hi")
    assert len(_FakeLoop.captured_init_kwargs["tool_specs"]) == expected_count


@pytest.mark.asyncio
async def test_tool_spec_paths_override_replaces_the_default_tool_set(tmp_path):
    schema_path = tmp_path / "tool_ping.json"
    schema_path.write_text(
        json.dumps({"name": "ping", "description": "ping", "input_schema": {"type": "object", "properties": {}}})
    )
    runtime = _runtime(
        tool_sets={
            "unified": ToolSetBundle(
                tool_spec_paths=[schema_path],
                dispatch_factory=lambda _ctx: DispatchBundle(dispatch=runtime_module.execute_tool),
                system_prompt_builder=_fake_build_system_prompt_sections,
            )
        }
    )
    await runtime.handle_query("hi")
    assert [t.name for t in _FakeLoop.captured_init_kwargs["tool_specs"]] == ["ping"]


@pytest.mark.asyncio
async def test_default_dispatch_is_execute_tool(monkeypatch):
    calls = []

    async def fake_execute_tool(name, args, *, nucore_interface):
        calls.append((name, args))
        return {"from": "default"}

    monkeypatch.setattr(runtime_module, "execute_tool", fake_execute_tool)
    runtime = _runtime()
    await runtime.handle_query("hi")
    dispatch = _FakeLoop.captured_init_kwargs["dispatch"]
    result = await dispatch("some_tool", {"a": 1})
    assert result == {"from": "default"}
    assert calls == [("some_tool", {"a": 1})]


@pytest.mark.asyncio
async def test_dispatch_override_is_used_instead_of_default_execute_tool():
    calls = []

    async def fake_dispatch(name, args):
        calls.append((name, args))
        return {"from": "override"}

    runtime = _runtime(
        tool_sets={
            "unified": ToolSetBundle(
                tool_spec_paths=None,
                dispatch_factory=lambda _ctx: DispatchBundle(dispatch=fake_dispatch),
                system_prompt_builder=_fake_build_system_prompt_sections,
            )
        }
    )
    await runtime.handle_query("hi")
    dispatch = _FakeLoop.captured_init_kwargs["dispatch"]
    result = await dispatch("some_tool", {"a": 1})
    assert result == {"from": "override"}
    assert calls == [("some_tool", {"a": 1})]


@pytest.mark.asyncio
async def test_system_prompt_builder_override_is_used_instead_of_default():
    async def fake_builder(nucore_interface):
        return ["dev-tools-section"]

    runtime = _runtime(
        tool_sets={
            "unified": ToolSetBundle(
                tool_spec_paths=None,
                dispatch_factory=lambda _ctx: DispatchBundle(dispatch=runtime_module.execute_tool),
                system_prompt_builder=fake_builder,
            )
        }
    )
    await runtime.handle_query("hi")
    # Not ["static", "tail"] -- the module-level default _patch_collaborators
    # monkeypatches build_system_prompt_sections to, which would prove the
    # override was ignored.
    assert _FakeLoop.captured_system_prompt == ["dev-tools-section"]


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


class _RichOnceLoop:
    """Like _FakeLoop, but returns a non-empty, recognizable new_messages
    transcript (a tool_use/tool_result round trip) on its first run() call,
    a plain text-only turn on every call after that, and records every
    call's history_messages -- what's needed to prove turn 2 actually saw
    turn 1's rich transcript, not just the collapsed {query}/{response}
    text pair."""

    calls: list[dict] = []
    TOOL_USE_BLOCK = {"type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {"path": "x.py"}}
    TOOL_RESULT_BLOCK = {"type": "tool_result", "tool_use_id": "toolu_1", "content": "file contents"}

    def __init__(self, **kwargs):
        pass

    async def run(self, *, system_prompt, history_messages, user_message, llm_config):
        _RichOnceLoop.calls.append({"user_message": user_message, "history_messages": list(history_messages)})
        if len(_RichOnceLoop.calls) == 1:
            new_messages = [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": [_RichOnceLoop.TOOL_USE_BLOCK]},
                {"role": "user", "content": [_RichOnceLoop.TOOL_RESULT_BLOCK]},
                {"role": "assistant", "content": "turn 1 answer"},
            ]
            return "turn 1 answer", new_messages
        return "turn 2 answer", []


@pytest.mark.asyncio
async def test_history_messages_replays_the_prior_turns_rich_transcript_verbatim(monkeypatch):
    # The actual fix: a turn's full tool_use/tool_result transcript -- not
    # just its collapsed {query}/{response} text pair -- must survive into
    # the *next* turn's history_messages.
    monkeypatch.setattr(runtime_module, "AgenticLoop", _RichOnceLoop)
    _RichOnceLoop.calls = []
    runtime = _runtime()

    await runtime.handle_query("first query", session_id="rich-session")
    await runtime.handle_query("second query", session_id="rich-session")

    second_call = _RichOnceLoop.calls[1]
    assert second_call["history_messages"] == [
        {"role": "user", "content": "first query"},
        {"role": "assistant", "content": [_RichOnceLoop.TOOL_USE_BLOCK]},
        {"role": "user", "content": [_RichOnceLoop.TOOL_RESULT_BLOCK]},
        {"role": "assistant", "content": "turn 1 answer"},
    ]


@pytest.mark.asyncio
async def test_history_messages_falls_back_to_the_text_pair_when_a_turn_has_no_rich_messages(monkeypatch):
    # Every existing fake/stub AgenticLoop.run() returns new_messages == []
    # -- confirms that case still replays the plain {query}/{response} pair,
    # unchanged from before this feature existed.
    monkeypatch.setattr(runtime_module, "AgenticLoop", _RecordingLoop)
    _RecordingLoop.calls = []
    runtime = _runtime()

    await runtime.handle_query("first", session_id="plain-session")
    await runtime.handle_query("second", session_id="plain-session")

    second_call = _RecordingLoop.calls[1]
    assert second_call["history_messages"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "first-response"},
    ]


@pytest.mark.asyncio
async def test_persisted_rich_messages_keep_the_exact_wrapped_content_actually_sent(monkeypatch):
    # new_messages is persisted verbatim -- including its ui_context-wrapped
    # first element -- not swapped back to the clean query. A swap was
    # tried and measured (via the real prompt log) to defeat Anthropic's
    # prompt caching: the next turn's replayed prefix must be byte-identical
    # to what was actually sent (and cached) this turn to get a cache hit,
    # and the wrapped text is exactly what was actually sent.
    # ConversationTurn.query (a separate field, asserted below) is what
    # stays clean, for text-only fallback/compaction use.
    class _OneShotRichLoop:
        def __init__(self, **kwargs):
            pass

        async def run(self, *, system_prompt, history_messages, user_message, llm_config):
            return "answer", [
                {"role": "user", "content": user_message},
                {"role": "assistant", "content": "answer"},
            ]

    monkeypatch.setattr(runtime_module, "AgenticLoop", _OneShotRichLoop)
    runtime = _runtime()

    await runtime.handle_query("real query", session_id="ctx-session", framework_context={"screen": "home"})

    history = runtime.session_store.get("ctx-session::unified", max_turns=20)
    assert history.turns[-1].query == "real query"
    assert history.turns[-1].messages[0] == {
        "role": "user",
        "content": "<ui_context>{'screen': 'home'}</ui_context>\n\nreal query",
    }


@pytest.mark.asyncio
async def test_a_provider_mismatch_falls_back_to_the_text_pair_instead_of_replaying_incompatible_content(monkeypatch):
    # A turn's rich .messages is in whatever shape the provider that
    # produced it emits -- not portable across a provider change (e.g. a
    # live config edit swapping providers for this tool set between
    # reconnects, with the same shared ConversationHistory surviving).
    # current_provider must be a known, disagreeing value here -- None
    # (unknown) is deliberately permissive, covered by the fallback test
    # above, not a mismatch.
    monkeypatch.setattr(runtime_module, "AgenticLoop", _RecordingLoop)
    _RecordingLoop.calls = []
    runtime = _runtime({"supported_llms": {"unified": {"provider": "claude"}}})
    history = runtime.session_store.get("mismatch-session::unified", max_turns=20)
    history.append(
        "old query",
        "old response",
        [{"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "n", "input": {}}]}],
        provider="openai",
    )

    await runtime.handle_query("second", session_id="mismatch-session")

    call = _RecordingLoop.calls[0]
    assert call["history_messages"] == [
        {"role": "user", "content": "old query"},
        {"role": "assistant", "content": "old response"},
    ]


# --- extra_tools/on_raw_response passthrough (plugin_authoring's Claude-
# native web search path, run_unified_runtime.py's _build_plugin_authoring_tool_set) ---


@pytest.mark.asyncio
async def test_handle_query_forwards_extra_tools_and_on_raw_response_to_agentic_loop():
    native_tool = {"type": "web_search_20250305", "name": "web_search"}

    async def harvest(raw_response):
        pass

    runtime = _runtime(
        tool_sets={
            "unified": ToolSetBundle(
                tool_spec_paths=None,
                dispatch_factory=lambda _ctx: DispatchBundle(
                    dispatch=runtime_module.execute_tool, extra_tools=[native_tool], on_raw_response=harvest
                ),
                system_prompt_builder=_fake_build_system_prompt_sections,
            )
        }
    )
    await runtime.handle_query("hi")

    assert _FakeLoop.captured_init_kwargs["extra_tools"] == [native_tool]
    assert _FakeLoop.captured_init_kwargs["on_raw_response"] is harvest


@pytest.mark.asyncio
async def test_handle_query_defaults_extra_tools_and_on_raw_response_to_none():
    await _runtime().handle_query("hi")
    assert _FakeLoop.captured_init_kwargs["extra_tools"] is None
    assert _FakeLoop.captured_init_kwargs["on_raw_response"] is None


# --- default `unified` bundle's own native-web-search wiring (OpenAI's
# web_search tool on /v1/responses, mirrors plugin_authoring's Claude-native
# equivalent -- see runtime.py's UnifiedRuntime.__init__ and
# _record_openai_responses_web_search_evidence) ---


@pytest.mark.asyncio
async def test_default_unified_bundle_gets_openai_web_search_tool_when_provider_resolves_to_openai_responses():
    runtime_config = {"supported_llms": {"unified": {"provider": "openai-responses"}}}
    await _runtime(runtime_config).handle_query("hi")
    assert _FakeLoop.captured_init_kwargs["extra_tools"] == [{"type": "web_search"}]
    assert (
        _FakeLoop.captured_init_kwargs["on_raw_response"]
        is runtime_module._record_openai_responses_web_search_evidence
    )


@pytest.mark.asyncio
async def test_default_unified_bundle_has_no_extra_tools_for_a_non_responses_provider():
    runtime_config = {"supported_llms": {"unified": {"provider": "openai"}}}
    await _runtime(runtime_config).handle_query("hi")
    assert _FakeLoop.captured_init_kwargs["extra_tools"] is None
    assert _FakeLoop.captured_init_kwargs["on_raw_response"] is None


@pytest.mark.asyncio
async def test_handle_query_forwarding_passthrough_still_wins_over_the_default_unified_bundle():
    # A caller-supplied tool_sets (e.g. plugin_authoring's own bundle, or a
    # test double) replaces the default "unified" bundle entirely -- the
    # provider-resolution wiring only ever applies to the hardcoded
    # fallback built when the caller omits "unified" from tool_sets.
    runtime_config = {"supported_llms": {"unified": {"provider": "openai-responses"}}}
    runtime = _runtime(
        runtime_config,
        tool_sets={
            "unified": ToolSetBundle(
                tool_spec_paths=None,
                dispatch_factory=lambda _ctx: DispatchBundle(dispatch=runtime_module.execute_tool),
                system_prompt_builder=_fake_build_system_prompt_sections,
            )
        },
    )
    await runtime.handle_query("hi")
    assert _FakeLoop.captured_init_kwargs["extra_tools"] is None
    assert _FakeLoop.captured_init_kwargs["on_raw_response"] is None


class TestRecordOpenAIResponsesWebSearchEvidence:
    """``_record_openai_responses_web_search_evidence`` -- the on_raw_response
    hook itself, independent of how UnifiedRuntime wires it in."""

    @pytest.mark.asyncio
    async def test_logs_sources_from_a_completed_web_search_call_item(self, caplog):
        raw_response = {
            "output": [
                {
                    "type": "web_search_call",
                    "status": "completed",
                    "action": {
                        "type": "search",
                        "sources": [{"type": "url", "url": "https://example.com"}, {"type": "url", "url": "https://other.example"}],
                    },
                }
            ]
        }
        with caplog.at_level("INFO"):
            await runtime_module._record_openai_responses_web_search_evidence(raw_response)
        assert "https://example.com" in caplog.text
        assert "https://other.example" in caplog.text

    @pytest.mark.asyncio
    async def test_ignores_a_web_search_call_that_has_not_completed_yet(self, caplog):
        raw_response = {
            "output": [
                {
                    "type": "web_search_call",
                    "status": "searching",
                    "action": {"type": "search", "sources": [{"type": "url", "url": "https://example.com"}]},
                }
            ]
        }
        with caplog.at_level("INFO"):
            await runtime_module._record_openai_responses_web_search_evidence(raw_response)
        assert "https://example.com" not in caplog.text

    @pytest.mark.asyncio
    async def test_ignores_non_web_search_output_items(self, caplog):
        raw_response = {"output": [{"type": "function_call", "call_id": "c1", "name": "t", "arguments": "{}"}]}
        with caplog.at_level("INFO"):
            await runtime_module._record_openai_responses_web_search_evidence(raw_response)
        assert caplog.text == ""

    @pytest.mark.asyncio
    async def test_tolerates_a_raw_response_with_no_output_key(self):
        await runtime_module._record_openai_responses_web_search_evidence({"content": "hi"})
        await runtime_module._record_openai_responses_web_search_evidence("not a dict")


# --- resolve_llm_profile (extracted from _resolve_llm_config so
# run_unified_runtime.py can resolve the same provider before UnifiedRuntime
# even exists -- see run_unified_runtime.py's native-vs-fallback search
# selection) ---


def test_resolve_llm_profile_returns_empty_dict_when_nothing_configured():
    assert resolve_llm_profile({}) == {}


def test_resolve_llm_profile_prefers_the_preferred_key():
    config = {"supported_llms": {"unified": {"provider": "claude"}, "other": {"provider": "openai"}}}
    assert resolve_llm_profile(config, preferred_key="unified") == {"provider": "claude"}


def test_resolve_llm_profile_falls_back_to_first_key_when_preferred_is_absent():
    config = {"supported_llms": {"other": {"provider": "openai"}, "another": {"provider": "gemini"}}}
    assert resolve_llm_profile(config, preferred_key="unified") == {"provider": "openai"}


def test_resolve_llm_profile_falls_back_to_first_key_when_nothing_else_matches():
    config = {"supported_llms": {"only_one": {"provider": "gemini"}}}
    assert resolve_llm_profile(config, preferred_key="unified") == {"provider": "gemini"}


# --- Mid-round tool-set switching (design/developers/merged-toolsets.md) ---


@pytest.mark.asyncio
async def test_handle_query_chains_to_the_other_tool_set_on_switch_request():
    from unified.loop import SWITCH_TOOL_SET_KEY, ToolSetSwitchRequested

    class _SwitchOnceLoop:
        calls = 0

        def __init__(self, **kwargs):
            self._dispatch = kwargs["dispatch"]

        async def run(self, *, system_prompt, history_messages, user_message, llm_config):
            _SwitchOnceLoop.calls += 1
            if _SwitchOnceLoop.calls == 1:
                result = await self._dispatch("switch_to_developer_mode", {})
                if isinstance(result, dict) and SWITCH_TOOL_SET_KEY in result:
                    raise ToolSetSwitchRequested(result[SWITCH_TOOL_SET_KEY])
            return "answered-in-" + ("unified" if _SwitchOnceLoop.calls == 1 else "plugin_authoring"), []

    import unified.runtime as rm

    original_loop = rm.AgenticLoop
    rm.AgenticLoop = _SwitchOnceLoop
    _SwitchOnceLoop.calls = 0
    try:
        runtime_config = {
            "enabled_profiles": ["unified", "plugin_authoring"],
            "default_profile_name": "unified",
            "max_tool_set_switches_per_turn": 1,
            "nucore_runtime": {"unified": {}, "plugin_authoring": {}},
        }
        runtime = _runtime(
            runtime_config,
            tool_sets={
                "plugin_authoring": ToolSetBundle(
                    tool_spec_paths=None,
                    dispatch_factory=lambda _ctx: DispatchBundle(dispatch=rm.execute_tool),
                    system_prompt_builder=_fake_build_system_prompt_sections,
                )
            },
        )
        result = await runtime.handle_query("hi")
        assert result.output["text"] == "answered-in-plugin_authoring"
        assert result.output["active_tool_set"] == "plugin_authoring"
        assert _SwitchOnceLoop.calls == 2
    finally:
        rm.AgenticLoop = original_loop


@pytest.mark.asyncio
async def test_handoff_summary_is_prepended_to_the_new_tool_sets_user_message():
    # Regression: a mid-round switch used to blindly resend the bare
    # trigger message (e.g. a lone "4" replying to a menu the new tool set
    # never saw) with zero context -- see merged-toolsets.md's "Mid-round
    # chaining". The switch tool's handoff_summary must reach the second
    # loop's user_message, while the persisted history still stores the
    # clean, original query (never the wrapped version).
    from unified.loop import SWITCH_TOOL_SET_KEY, ToolSetSwitchRequested

    captured_user_messages = []

    class _SwitchOnceLoop:
        calls = 0

        def __init__(self, **kwargs):
            self._dispatch = kwargs["dispatch"]

        async def run(self, *, system_prompt, history_messages, user_message, llm_config):
            _SwitchOnceLoop.calls += 1
            captured_user_messages.append(user_message)
            if _SwitchOnceLoop.calls == 1:
                # Simulates the model's own tool call actually supplying the
                # (schema-required) handoff_summary argument -- the real
                # switch handler built by _build_loop/_wrap_dispatch_for_
                # switching is what reads it back out and puts it on the
                # sentinel dict, not anything this test controls directly.
                result = await self._dispatch(
                    "switch_to_developer_mode", {"handoff_summary": "customer wants a SimpliSafe plugin"}
                )
                if isinstance(result, dict) and SWITCH_TOOL_SET_KEY in result:
                    raise ToolSetSwitchRequested(
                        result[SWITCH_TOOL_SET_KEY], handoff_summary=result.get("handoff_summary")
                    )
            return "answered-in-plugin_authoring", []

    import unified.runtime as rm

    original_loop = rm.AgenticLoop
    rm.AgenticLoop = _SwitchOnceLoop
    _SwitchOnceLoop.calls = 0
    try:
        runtime_config = {
            "enabled_profiles": ["unified", "plugin_authoring"],
            "default_profile_name": "unified",
            "max_tool_set_switches_per_turn": 1,
            "nucore_runtime": {"unified": {}, "plugin_authoring": {}},
        }
        runtime = _runtime(
            runtime_config,
            tool_sets={
                "plugin_authoring": ToolSetBundle(
                    tool_spec_paths=None,
                    dispatch_factory=lambda _ctx: DispatchBundle(dispatch=rm.execute_tool),
                    system_prompt_builder=_fake_build_system_prompt_sections,
                )
            },
        )
        result = await runtime.handle_query("4")

        assert captured_user_messages[0] == "4"  # first loop: no switch has happened yet
        assert "customer wants a SimpliSafe plugin" in captured_user_messages[1]
        assert captured_user_messages[1].endswith("\n\n4")  # original query still present, verbatim

        history = runtime.session_store.get("default::plugin_authoring", max_turns=20)
        assert history.turns[-1].query == "4"  # persisted history stays clean, not the wrapped version
        assert result.output["text"] == "answered-in-plugin_authoring"
    finally:
        rm.AgenticLoop = original_loop

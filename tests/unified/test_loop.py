"""AgenticLoop -- regression test for tool-call dispatch ordering.

Multiple tool calls returned in the same model turn used to be dispatched
via asyncio.gather, which was harmless only because every IoXWrapper HTTP
call was fake-async (a blocking `requests` call dressed up in an async def)
and accidentally serialized everything anyway. Once HTTP calls became
genuinely async, gather let same-turn calls truly interleave -- e.g. a
multi_device_scene role-check racing ahead of the pair_device call that adds
the very device it's checking, reporting "not available" for a device that
in fact was, moments later. Tool calls within one turn can have real
ordering dependencies (see plan_common.md's own "one at a time" rule, which
the model doesn't reliably follow) -- dispatch must be sequential.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

import unified.loop as loop_module
from unified.loop import AgenticLoop


class _FakeAdapter:
    def __init__(self, responses):
        self._responses = iter(responses)

    def export_tools(self, specs):
        return []

    async def generate(self, *, messages, config, tools):
        return next(self._responses)

    def build_tool_round_trip_messages(self, *, raw_response, tool_calls, tool_results, config):
        return [{"role": "assistant", "content": "tool round trip"}]


async def test_tool_calls_in_one_turn_dispatch_sequentially_not_concurrently():
    order: list[str] = []

    async def fake_dispatch(name, args):
        order.append(f"{name}:start")
        if name == "slow":
            await asyncio.sleep(0.05)
        order.append(f"{name}:end")
        return {"ok": True}

    responses = [
        {"tool_calls": [
            {"id": "1", "name": "slow", "input": {}},
            {"id": "2", "name": "fast", "input": {}},
        ]},
        {"text": "done"},
    ]
    adapter = _FakeAdapter(responses)
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=fake_dispatch)

    final_text, _ = await loop.run(system_prompt="sys", history_messages=[], user_message="hi")

    assert final_text == "done"
    # If dispatch used asyncio.gather, "fast" (no await inside) would run to
    # completion while "slow" was still sleeping, giving
    # ["slow:start", "fast:start", "fast:end", "slow:end"] instead.
    assert order == ["slow:start", "slow:end", "fast:start", "fast:end"]


async def test_generate_usage_is_logged_per_round(monkeypatch):
    # Each round's cache_xxx usage must reach the prompt log tagged with that
    # same round's intent name, so it can be correlated back to the request
    # that produced it (see PromptLogManager.write_usage).
    logged: list[tuple[str, dict]] = []

    class _FakeManager:
        async def write(self, *a, **kw):
            pass

        async def write_usage(self, intent_name, usage):
            logged.append((intent_name, usage))

    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: _FakeManager())

    responses = [
        {"tool_calls": [{"id": "1", "name": "noop", "input": {}}], "usage": {"cache_read_input_tokens": 14000}},
        {"text": "done", "usage": {"cache_read_input_tokens": 14000, "cache_creation_input_tokens": 0}},
    ]
    adapter = _FakeAdapter(responses)
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=lambda name, args: _ok())

    await loop.run(system_prompt="sys", history_messages=[], user_message="hi")

    assert logged == [
        ("unified (round 1)", {"cache_read_input_tokens": 14000}),
        ("unified (round 2)", {"cache_read_input_tokens": 14000, "cache_creation_input_tokens": 0}),
    ]


async def test_list_system_prompt_emits_one_system_message_per_section_in_order(monkeypatch):
    # prompt_builder splits the prompt into [static, volatile tail]; each
    # section must reach the adapter as its own system message, in order,
    # ahead of history -- that's what lets claude_adapter cache the static
    # part independently of the tail. Empty sections are dropped.
    class _FakeManager:
        async def write(self, *a, **kw):
            pass

        async def write_usage(self, *a, **kw):
            pass

    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: _FakeManager())
    captured: list[list[dict]] = []

    class _CapturingAdapter(_FakeAdapter):
        async def generate(self, *, messages, config, tools):
            captured.append(list(messages))
            return await super().generate(messages=messages, config=config, tools=tools)

    loop = AgenticLoop(llm_client=_CapturingAdapter([{"text": "done"}]), tool_specs=[], dispatch=lambda n, a: _ok())

    await loop.run(
        system_prompt=["static part", "", "volatile tail"],
        history_messages=[{"role": "user", "content": "earlier"}, {"role": "assistant", "content": "ok"}],
        user_message="hi",
    )

    assert captured[0] == [
        {"role": "system", "content": "static part"},
        {"role": "system", "content": "volatile tail"},
        {"role": "user", "content": "earlier"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "hi"},
    ]


async def test_final_answer_is_logged_within_its_own_turn(monkeypatch):
    # write() used to only ever be called before a generate() call, so a
    # turn's actual final answer was never captured live -- only recovered
    # (if at all) retroactively via the next turn's reconstructed history.
    calls: list[tuple[str, list, str | None]] = []

    class _FakeManager:
        async def write(self, intent_name, messages, *, conversation_id=None):
            calls.append((intent_name, list(messages), conversation_id))

        async def write_usage(self, *a, **kw):
            pass

    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: _FakeManager())

    adapter = _FakeAdapter([{"text": "done"}])
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=lambda n, a: _ok())

    await loop.run(
        system_prompt="sys",
        history_messages=[],
        user_message="hi",
        llm_config={"session_id": "conv-1"},
    )

    assert len(calls) == 2  # the round-1 request, then the final answer
    final_intent, final_messages, final_conversation_id = calls[-1]
    assert final_intent == "unified (round 1)"
    assert final_conversation_id == "conv-1"
    assert final_messages[-1] == {"role": "assistant", "content": "done"}


class _FlagRecordingManager:
    """Records write()/write_flag() calls; used by the fabrication-guard
    tests below. write() itself is a no-op -- these tests only care about
    what the guard did, not the ordinary request/response log lines."""

    def __init__(self):
        self.flags: list[tuple[str, str, dict]] = []

    async def write(self, *a, **kw):
        pass

    async def write_usage(self, *a, **kw):
        pass

    async def write_flag(self, intent_name, flag_kind, detail, *, conversation_id=None):
        self.flags.append((intent_name, flag_kind, detail))


async def test_max_tokens_truncation_returns_explicit_notice_not_partial_text(monkeypatch):
    manager = _FlagRecordingManager()
    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: manager)

    adapter = _FakeAdapter([{"stop_reason": "max_tokens", "text": "The light is now o"}])
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=lambda n, a: _ok())

    final_text, new_messages = await loop.run(system_prompt="sys", history_messages=[], user_message="turn it off")

    assert final_text == loop_module._TRUNCATED_TEXT
    assert final_text != "The light is now o"
    assert any(f[1] == "truncated_max_tokens" for f in manager.flags)
    assert new_messages[-1] == {"role": "assistant", "content": loop_module._TRUNCATED_TEXT}


async def test_max_tokens_truncation_never_dispatches_a_cut_off_tool_call(monkeypatch):
    manager = _FlagRecordingManager()
    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: manager)

    dispatched: list[str] = []

    async def fake_dispatch(name, args):
        dispatched.append(name)
        return {"ok": True}

    adapter = _FakeAdapter([
        {"stop_reason": "max_tokens", "tool_calls": [{"id": "1", "name": "send_command", "input": {}}]},
    ])
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=fake_dispatch)

    final_text, _ = await loop.run(system_prompt="sys", history_messages=[], user_message="turn it off")

    assert final_text == loop_module._TRUNCATED_TEXT
    assert dispatched == []


async def test_pause_turn_resends_paused_content_and_continues_instead_of_returning(monkeypatch):
    # Regression test for the production incident: a web_search that hadn't
    # finished when Anthropic paused the turn (stop_reason="pause_turn") used
    # to fall through to the "no tool calls -> final answer" branch, which
    # persisted the lone server_tool_use block (no matching
    # web_search_tool_result) as a *final* assistant message -- corrupting
    # all later history replays of this session. The fix must instead resend
    # that same content and let the loop continue to a real next round.
    manager = _FlagRecordingManager()
    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: manager)

    orphaned_search_block = {"type": "server_tool_use", "name": "web_search", "input": {"query": "..."}}
    generate_calls: list[dict] = []

    class _PausingThenDoneAdapter(_SearchCapableAdapter):
        def __init__(self):
            super().__init__([])
            self._n = 0

        async def generate(self, *, messages, config, tools):
            generate_calls.append({"messages": list(messages)})
            self._n += 1
            if self._n == 1:
                return {
                    "stop_reason": "pause_turn",
                    "content": [orphaned_search_block],
                    "search_evidence": orphaned_search_block,
                    "text": "",
                }
            return {"text": "Done searching."}

    loop = AgenticLoop(llm_client=_PausingThenDoneAdapter(), tool_specs=[], dispatch=lambda n, a: _ok())

    final_text, new_messages = await loop.run(system_prompt="sys", history_messages=[], user_message="search it")

    assert final_text == "Done searching."
    # The loop actually continued -- generate() was called a second time with
    # the paused content folded back in, not returned as a final answer.
    assert len(generate_calls) == 2
    assert generate_calls[1]["messages"][-1] == {
        "role": "assistant",
        "content": [orphaned_search_block, {"type": "text", "text": ""}],
    }
    # The orphaned block only ever appears as a *mid-turn* message, always
    # immediately followed by the real final answer -- never persisted as
    # the last (and therefore replayed-as-final) message of the turn.
    assert new_messages[-1] == {"role": "assistant", "content": "Done searching."}
    assert not any(
        isinstance(m.get("content"), list) and orphaned_search_block in m["content"]
        for m in new_messages[-1:]
    )


async def test_fabrication_guard_flags_zero_tool_call_completion_claim(monkeypatch):
    manager = _FlagRecordingManager()
    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: manager)

    adapter = _FakeAdapter([{"text": "Done! I've turned off the light."}])
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=lambda n, a: _ok())

    final_text, _ = await loop.run(system_prompt="sys", history_messages=[], user_message="turn it off")

    assert [f for f in manager.flags if f[1] == "fabrication_flag"]
    # mode defaults to "log" -- detection never changes what's returned.
    assert final_text == "Done! I've turned off the light."


async def test_fabrication_guard_does_not_fire_when_tool_was_called(monkeypatch):
    manager = _FlagRecordingManager()
    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: manager)

    responses = [
        {"tool_calls": [{"id": "1", "name": "send_command", "input": {}}]},
        {"text": "Done! The light is now off."},
    ]
    adapter = _FakeAdapter(responses)
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=lambda n, a: _ok())

    final_text, _ = await loop.run(system_prompt="sys", history_messages=[], user_message="turn it off")

    assert manager.flags == []
    assert final_text == "Done! The light is now off."


async def test_fabrication_guard_does_not_fire_on_legitimate_replies(monkeypatch):
    manager = _FlagRecordingManager()
    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: manager)

    for text in [
        "I need you to specify which notification you'd like to send.",
        "The GuestBathroom is currently on at 100% brightness.",
    ]:
        adapter = _FakeAdapter([{"text": text}])
        loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=lambda n, a: _ok())
        await loop.run(system_prompt="sys", history_messages=[], user_message="hi")

    assert manager.flags == []


async def test_fabrication_guard_block_mode_retries_and_returns_grounded_reply(monkeypatch):
    manager = _FlagRecordingManager()
    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: manager)

    responses = [
        {"text": "Done! I've turned off the light."},  # round 1: fabricated, no tool call
        {"tool_calls": [{"id": "1", "name": "send_command", "input": {}}]},  # retry: real call
        {"text": "The light is now off."},  # grounded by this turn's own tool call
    ]
    adapter = _FakeAdapter(responses)
    loop = AgenticLoop(
        llm_client=adapter,
        tool_specs=[],
        dispatch=lambda n, a: _ok(),
        fabrication_guard_mode="block",
    )

    streamed: list[str] = []

    async def fake_stream_handler(chunk, is_end=False):
        streamed.append(chunk)

    final_text, new_messages = await loop.run(
        system_prompt="sys",
        history_messages=[],
        user_message="turn it off",
        llm_config={"stream_handler": fake_stream_handler},
    )

    assert final_text == loop_module._FABRICATION_RETRY_NOTICE + "The light is now off."
    assert any(f[1] == "fabrication_flag" for f in manager.flags)
    assert any("system note" in m.get("content", "") for m in new_messages if m.get("role") == "user")
    # The correction streams live, immediately, since a customer may already
    # have seen the fabricated round's own text stream out before this check
    # ever ran -- not silently folded into the eventual final answer only.
    assert loop_module._FABRICATION_RETRY_NOTICE in streamed


async def test_fabrication_guard_block_mode_exhausts_retries_and_falls_back_safely(monkeypatch):
    manager = _FlagRecordingManager()
    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: manager)

    generate_calls = 0

    class _AlwaysFabricatingAdapter(_FakeAdapter):
        async def generate(self, *, messages, config, tools):
            nonlocal generate_calls
            generate_calls += 1
            return {"text": "Done! I've turned off the light."}

    loop = AgenticLoop(
        llm_client=_AlwaysFabricatingAdapter([]),
        tool_specs=[],
        dispatch=lambda n, a: _ok(),
        fabrication_guard_mode="block",
        max_fabrication_retries=1,
    )

    final_text, _ = await loop.run(system_prompt="sys", history_messages=[], user_message="turn it off")

    assert final_text == loop_module._FABRICATION_RETRY_NOTICE + loop_module._FABRICATION_FALLBACK_TEXT
    assert final_text != "Done! I've turned off the light."
    assert generate_calls == 2  # max_fabrication_retries(1) + the initial round
    assert any(f[1] == "fabrication_retry_exhausted" for f in manager.flags)


class _SearchCapableAdapter(_FakeAdapter):
    """Like _FakeAdapter, but also implements build_text_round_trip_messages
    -- the real adapters' hook this module's no-tool-calls branch now calls
    so a search-only round's provenance survives into later rounds' history
    instead of collapsing to plain text. See the production-log investigation
    behind this fix: a model that genuinely searched, with no record of that
    left in history once the round produced no dispatchable tool_use, later
    had no way to tell whether it had searched and guessed wrong."""

    def build_text_round_trip_messages(self, *, raw_response, text):
        search_evidence = raw_response.get("search_evidence")
        if search_evidence is not None:
            return [{"role": "assistant", "content": [search_evidence, {"type": "text", "text": text}]}]
        return [{"role": "assistant", "content": text}]


async def test_search_only_round_preserves_provenance_in_returned_new_messages(monkeypatch):
    # new_messages is exactly what a caller (e.g. UnifiedRuntime.handle_query)
    # persists as this turn's contribution to history, and feeds back in as
    # `history_messages` on the *next* turn -- so this is the direct
    # regression test for "a later round can see that a search happened,"
    # without needing to actually run a second turn.
    manager = _FlagRecordingManager()
    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: manager)

    search_evidence = {"type": "server_tool_use", "name": "web_search", "input": {"query": "dst canada"}}
    adapter = _SearchCapableAdapter(
        [{"text": "Yes, several Canadian regions changed their rules.", "search_evidence": search_evidence}]
    )
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=lambda n, a: _ok())

    final_text, new_messages = await loop.run(
        system_prompt="sys", history_messages=[], user_message="did dst rules change?"
    )

    assert final_text == "Yes, several Canadian regions changed their rules."
    assistant_message = new_messages[-1]
    assert isinstance(assistant_message["content"], list)
    assert search_evidence in assistant_message["content"]


async def test_fallback_substituted_text_is_stored_as_plain_string_not_rich_blocks(monkeypatch):
    # When the fabrication guard replaces `text` with its hardcoded
    # fallback, the original raw_response's content (which may include the
    # very claim being discarded) must not be what gets persisted instead.
    manager = _FlagRecordingManager()
    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: manager)

    class _AlwaysFabricatingSearchAdapter(_SearchCapableAdapter):
        async def generate(self, *, messages, config, tools):
            return {
                "text": "Done! I've turned off the light.",
                "search_evidence": {"type": "server_tool_use", "name": "web_search", "input": {}},
            }

    loop = AgenticLoop(
        llm_client=_AlwaysFabricatingSearchAdapter([]),
        tool_specs=[],
        dispatch=lambda n, a: _ok(),
        fabrication_guard_mode="block",
        max_fabrication_retries=1,
    )

    final_text, new_messages = await loop.run(system_prompt="sys", history_messages=[], user_message="turn it off")

    assert final_text == loop_module._FABRICATION_RETRY_NOTICE + loop_module._FABRICATION_FALLBACK_TEXT
    assert new_messages[-1] == {"role": "assistant", "content": final_text}


async def test_ordinary_reply_history_is_unchanged_when_adapter_has_no_search_hook():
    # _FakeAdapter has no build_text_round_trip_messages at all -- the
    # getattr(..., None) fallback in loop.py must keep producing exactly
    # today's flat-string shape for any such adapter/test-double.
    adapter = _FakeAdapter([{"text": "Sure, done."}])
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=lambda n, a: _ok())

    final_text, new_messages = await loop.run(system_prompt="sys", history_messages=[], user_message="hi")

    assert final_text == "Sure, done."
    assert new_messages[-1] == {"role": "assistant", "content": "Sure, done."}


async def test_extra_tools_are_appended_after_exported_tools():
    # plugin_authoring's Claude-native web search path (run_unified_runtime.py)
    # injects a raw server-tool dict this way, bypassing export_tools()/
    # ToolSpec entirely -- nothing to dispatch for a server tool.
    captured_tools: list[list[dict]] = []

    class _CapturingAdapter(_FakeAdapter):
        async def generate(self, *, messages, config, tools):
            captured_tools.append(tools)
            return await super().generate(messages=messages, config=config, tools=tools)

    native_tool = {"type": "web_search_20250305", "name": "web_search"}
    loop = AgenticLoop(
        llm_client=_CapturingAdapter([{"text": "done"}]),
        tool_specs=[],
        dispatch=lambda n, a: _ok(),
        extra_tools=[native_tool],
    )

    await loop.run(system_prompt="sys", history_messages=[], user_message="hi")

    assert captured_tools[0] == [native_tool]


async def test_on_raw_response_is_awaited_once_per_round():
    seen: list[Any] = []

    async def harvest(raw_response):
        seen.append(raw_response)

    responses = [
        {"tool_calls": [{"id": "1", "name": "noop", "input": {}}]},
        {"text": "done"},
    ]
    adapter = _FakeAdapter(responses)
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=lambda n, a: _ok(), on_raw_response=harvest)

    await loop.run(system_prompt="sys", history_messages=[], user_message="hi")

    assert seen == responses


async def test_on_raw_response_exception_is_swallowed_not_propagated():
    async def harvest(raw_response):
        raise ValueError("boom")

    adapter = _FakeAdapter([{"text": "done"}])
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=lambda n, a: _ok(), on_raw_response=harvest)

    final_text, _ = await loop.run(system_prompt="sys", history_messages=[], user_message="hi")

    assert final_text == "done"


async def _ok():
    return {"ok": True}


# --- Mid-round tool-set switching (design/developers/merged-toolsets.md) ---


async def test_switch_tool_set_result_raises_instead_of_continuing_the_round():
    from unified.loop import SWITCH_TOOL_SET_KEY, ToolSetSwitchRequested

    async def fake_dispatch(name, args):
        return {SWITCH_TOOL_SET_KEY: "plugin_authoring"}

    responses = [{"tool_calls": [{"id": "1", "name": "switch_to_developer_mode", "input": {}}]}]
    adapter = _FakeAdapter(responses)
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=fake_dispatch)

    with pytest.raises(ToolSetSwitchRequested) as exc_info:
        await loop.run(system_prompt="sys", history_messages=[], user_message="hi")

    assert exc_info.value.target_tool_set == "plugin_authoring"


async def test_switch_tool_set_result_carries_the_handoff_summary_through():
    from unified.loop import SWITCH_TOOL_SET_KEY, ToolSetSwitchRequested

    async def fake_dispatch(name, args):
        return {SWITCH_TOOL_SET_KEY: "plugin_authoring", "handoff_summary": "customer wants a SimpliSafe plugin"}

    responses = [{"tool_calls": [{"id": "1", "name": "switch_to_developer_mode", "input": {}}]}]
    adapter = _FakeAdapter(responses)
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=fake_dispatch)

    with pytest.raises(ToolSetSwitchRequested) as exc_info:
        await loop.run(system_prompt="sys", history_messages=[], user_message="hi")

    assert exc_info.value.handoff_summary == "customer wants a SimpliSafe plugin"


async def test_switch_tool_set_without_a_handoff_summary_leaves_it_none():
    from unified.loop import SWITCH_TOOL_SET_KEY, ToolSetSwitchRequested

    async def fake_dispatch(name, args):
        return {SWITCH_TOOL_SET_KEY: "plugin_authoring"}

    responses = [{"tool_calls": [{"id": "1", "name": "switch_to_developer_mode", "input": {}}]}]
    adapter = _FakeAdapter(responses)
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=fake_dispatch)

    with pytest.raises(ToolSetSwitchRequested) as exc_info:
        await loop.run(system_prompt="sys", history_messages=[], user_message="hi")

    assert exc_info.value.handoff_summary is None


async def test_switch_tool_set_does_not_dispatch_remaining_calls_in_the_same_round():
    from unified.loop import SWITCH_TOOL_SET_KEY, ToolSetSwitchRequested

    dispatched = []

    async def fake_dispatch(name, args):
        dispatched.append(name)
        if name == "switch_to_developer_mode":
            return {SWITCH_TOOL_SET_KEY: "plugin_authoring"}
        return {"ok": True}

    responses = [
        {
            "tool_calls": [
                {"id": "1", "name": "switch_to_developer_mode", "input": {}},
                {"id": "2", "name": "some_other_tool", "input": {}},
            ]
        }
    ]
    adapter = _FakeAdapter(responses)
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=fake_dispatch)

    with pytest.raises(ToolSetSwitchRequested):
        await loop.run(system_prompt="sys", history_messages=[], user_message="hi")

    assert dispatched == ["switch_to_developer_mode"]


async def test_an_ordinary_error_result_does_not_raise_or_unwind():
    # A disabled target or an exhausted switch cap (tool_set_switch.py) is
    # just a plain {"error": ...} dict -- the same convention every other
    # tool handler uses -- and must NOT raise ToolSetSwitchRequested; the
    # loop should treat it like any other tool result and keep going.
    async def fake_dispatch(name, args):
        return {"error": "developer tools are not enabled on this installation"}

    responses = [
        {"tool_calls": [{"id": "1", "name": "switch_to_developer_mode", "input": {}}]},
        {"text": "explained the error"},
    ]
    adapter = _FakeAdapter(responses)
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=fake_dispatch)

    final_text, _ = await loop.run(system_prompt="sys", history_messages=[], user_message="hi")

    assert final_text == "explained the error"


# --- summarize(): a fork of the round-1 request, ending in an instruction ---


class _RecordingAdapter(_FakeAdapter):
    def __init__(self, response):
        super().__init__([])
        self._response = response
        self.calls: list[dict] = []

    def export_tools(self, specs):
        return [{"name": "t1"}]

    async def generate(self, *, messages, config, tools):
        self.calls.append({"messages": messages, "config": config, "tools": tools})
        return self._response


async def _noop_dispatch(name, args):
    return {}


async def test_summarize_forks_the_round_one_request_and_ends_with_the_instruction():
    adapter = _RecordingAdapter({"text": "  the summary  "})
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=_noop_dispatch)
    history = [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]
    live_config = {"model": "m", "stream": True, "stream_handler": lambda chunk: None, "session_id": "s"}

    text = await loop.summarize(
        system_prompt=["static", "tail"],
        history_messages=history,
        instruction="summarize now",
        llm_config=live_config,
        max_tokens=777,
    )

    assert text == "the summary"
    sent = adapter.calls[0]
    assert sent["messages"] == [
        {"role": "system", "content": "static"},
        {"role": "system", "content": "tail"},
        *history,
        {"role": "user", "content": "summarize now"},
    ]
    assert sent["tools"] == [{"name": "t1"}]  # the loop's own exported tools, so the cached prefix matches
    cfg = sent["config"]
    assert cfg["tool_choice"] == {"type": "none"}
    assert cfg["cache_messages"] is True
    assert cfg["stream"] is False and "stream_handler" not in cfg
    assert cfg["max_tokens"] == 777 and cfg["model"] == "m" and cfg["session_id"] == "s"
    # The caller's own config and history list are untouched.
    assert live_config["stream"] is True and "stream_handler" in live_config
    assert len(history) == 2


async def test_summarize_accepts_a_single_string_system_prompt_and_returns_empty_when_there_is_no_text():
    adapter = _RecordingAdapter(None)
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=_noop_dispatch)

    text = await loop.summarize(system_prompt="sys", history_messages=[], instruction="go", max_tokens=10)

    assert text == ""
    assert adapter.calls[0]["messages"][0] == {"role": "system", "content": "sys"}


async def test_on_usage_hears_every_round_and_a_failing_hook_does_not_break_the_turn():
    heard: list = []

    def on_usage(kind, usage):
        heard.append((kind, usage))
        raise RuntimeError("boom")

    responses = [
        {"tool_calls": [{"id": "1", "name": "noop", "input": {}}], "usage": {"input_tokens": 5}},
        {"text": "done", "usage": {"input_tokens": 9}},
    ]
    loop = AgenticLoop(llm_client=_FakeAdapter(responses), tool_specs=[], dispatch=lambda name, args: _ok())

    text, _ = await loop.run(system_prompt="s", history_messages=[], user_message="hi", on_usage=on_usage)

    assert text == "done"
    assert heard == [("round", {"input_tokens": 5}), ("round", {"input_tokens": 9})]


async def test_summarize_reports_its_usage_as_a_compaction_fork():
    heard: list = []
    adapter = _RecordingAdapter({"text": "sum", "usage": {"input_tokens": 3}})
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=lambda name, args: _ok())

    await loop.summarize(
        system_prompt="s",
        history_messages=[],
        instruction="go",
        max_tokens=10,
        on_usage=lambda kind, usage: heard.append((kind, usage)),
    )

    assert heard == [("compaction fork", {"input_tokens": 3})]


class _EchoingAdapter(_FakeAdapter):
    """Puts each tool result into the transcript, so the turn actually grows."""

    def build_tool_round_trip_messages(self, *, raw_response, tool_calls, tool_results, config):
        return [{"role": "user", "content": str(tool_results)}]


def _big_tool_rounds(rounds: int, chars: int):
    responses = [{"tool_calls": [{"id": str(i), "name": "noop", "input": {}}]} for i in range(rounds)]
    return [*responses, {"text": "done"}]


async def test_mid_turn_budget_reports_once_per_crossing_and_changes_nothing():
    seen: list = []

    async def dispatch(name, args):
        return {"blob": "x" * 3000}

    adapter = _EchoingAdapter(_big_tool_rounds(4, 3000))
    loop = AgenticLoop(llm_client=adapter, tool_specs=[], dispatch=dispatch)

    text, new_messages = await loop.run(
        system_prompt="s",
        history_messages=[],
        user_message="hi",
        mid_turn_budget=500,
        on_mid_turn_over_budget=lambda rnd, tokens, budget: seen.append((rnd, tokens, budget)),
    )

    assert text == "done"
    assert seen and seen[0][0] == 2 and all(b == 500 for _, _, b in seen)
    assert len(seen) == len({t // 500 for _, t, _ in seen})  # one report per budget multiple
    baseline = AgenticLoop(llm_client=_EchoingAdapter(_big_tool_rounds(4, 3000)), tool_specs=[], dispatch=dispatch)
    _, expected = await baseline.run(system_prompt="s", history_messages=[], user_message="hi")
    assert new_messages == expected


async def test_mid_turn_budget_is_silent_for_a_small_turn_and_a_raising_hook_is_harmless():
    calls: list = []
    loop = AgenticLoop(llm_client=_FakeAdapter(_big_tool_rounds(2, 10)), tool_specs=[], dispatch=lambda n, a: _ok())
    await loop.run(
        system_prompt="s",
        history_messages=[],
        user_message="hi",
        mid_turn_budget=1_000_000,
        on_mid_turn_over_budget=lambda *a: calls.append(a),
    )
    assert calls == []

    def explode(*_):
        raise RuntimeError("boom")

    loop = AgenticLoop(llm_client=_EchoingAdapter(_big_tool_rounds(2, 10)), tool_specs=[], dispatch=lambda n, a: _ok())
    text, _ = await loop.run(
        system_prompt="s", history_messages=[], user_message="hi", mid_turn_budget=1, on_mid_turn_over_budget=explode
    )
    assert text == "done"

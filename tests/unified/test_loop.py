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

import unified.loop as loop_module
from unified.loop import AgenticLoop
from unified.provider_dispatch_adapter import ProviderDispatchLLMAdapter


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

    final_text, new_messages = await loop.run(system_prompt="sys", history_messages=[], user_message="turn it off")

    assert final_text == "The light is now off."
    assert any(f[1] == "fabrication_flag" for f in manager.flags)
    assert any("system note" in m.get("content", "") for m in new_messages if m.get("role") == "user")


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

    assert final_text == loop_module._FABRICATION_FALLBACK_TEXT
    assert final_text != "Done! I've turned off the light."
    assert generate_calls == 2  # max_fabrication_retries(1) + the initial round
    assert any(f[1] == "fabrication_retry_exhausted" for f in manager.flags)


class _ConfigCapturingAdapter(_FakeAdapter):
    """Records the config each generate() call actually received, so tests
    can check whether tool_choice got forced on a given round."""

    provider_name = "claude"

    def __init__(self, responses):
        super().__init__(responses)
        self.configs: list[dict] = []

    async def generate(self, *, messages, config, tools, expect_json: bool = False):
        # ProviderDispatchLLMAdapter.generate() always forwards expect_json
        # explicitly, unlike AgenticLoop's own direct calls -- accepted (and
        # ignored) here so the dispatch-routed test below can reuse this class.
        self.configs.append(config)
        return await super().generate(messages=messages, config=config, tools=tools)


async def test_fabrication_guard_block_mode_forces_tool_choice_on_claude_retry(monkeypatch):
    # auto tool_choice already let the model skip the call once this turn --
    # the retry should force it instead of asking auto to reconsider the
    # same way it just failed.
    manager = _FlagRecordingManager()
    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: manager)

    responses = [
        {"text": "Done! I've turned off the light."},  # round 1: fabricated, no tool call
        {"tool_calls": [{"id": "1", "name": "send_command", "input": {}}]},  # retry: real call
        {"text": "The light is now off."},
    ]
    adapter = _ConfigCapturingAdapter(responses)
    loop = AgenticLoop(
        llm_client=adapter,
        tool_specs=[],
        dispatch=lambda n, a: _ok(),
        fabrication_guard_mode="block",
    )

    final_text, _ = await loop.run(system_prompt="sys", history_messages=[], user_message="turn it off")

    assert final_text == "The light is now off."
    assert "tool_choice" not in adapter.configs[0]  # first attempt: left at auto
    assert adapter.configs[1]["tool_choice"] == {"type": "any"}  # retry: forced
    assert "tool_choice" not in adapter.configs[2]  # forced only for the one retried round


async def test_fabrication_guard_block_mode_does_not_force_tool_choice_on_non_claude(monkeypatch):
    # tool_choice shapes are provider-specific ({"type": "any"} is
    # Anthropic's) -- forcing it on another provider's adapter would be
    # silently wrong or outright rejected, so this must stay Claude-only.
    manager = _FlagRecordingManager()
    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: manager)

    class _OpenAIConfigCapturingAdapter(_ConfigCapturingAdapter):
        provider_name = "openai"

    responses = [
        {"text": "Done! I've turned off the light."},
        {"tool_calls": [{"id": "1", "name": "send_command", "input": {}}]},
        {"text": "The light is now off."},
    ]
    adapter = _OpenAIConfigCapturingAdapter(responses)
    loop = AgenticLoop(
        llm_client=adapter,
        tool_specs=[],
        dispatch=lambda n, a: _ok(),
        fabrication_guard_mode="block",
    )

    await loop.run(system_prompt="sys", history_messages=[], user_message="turn it off")

    assert all("tool_choice" not in c for c in adapter.configs)


async def test_tool_choice_escalation_resolves_through_dispatch_adapter(monkeypatch):
    # Production wires AgenticLoop to a ProviderDispatchLLMAdapter, whose own
    # provider_name is the constant "dispatch" -- the escalation must resolve
    # the *routed* client's provider_name, not the dispatcher's.
    manager = _FlagRecordingManager()
    monkeypatch.setattr(loop_module, "get_prompt_log_manager", lambda: manager)

    responses = [
        {"text": "Done! I've turned off the light."},
        {"tool_calls": [{"id": "1", "name": "send_command", "input": {}}]},
        {"text": "The light is now off."},
    ]
    claude_adapter = _ConfigCapturingAdapter(responses)
    dispatch_adapter = ProviderDispatchLLMAdapter(clients={"claude": claude_adapter})
    loop = AgenticLoop(
        llm_client=dispatch_adapter,
        tool_specs=[],
        dispatch=lambda n, a: _ok(),
        fabrication_guard_mode="block",
    )

    await loop.run(
        system_prompt="sys",
        history_messages=[],
        user_message="turn it off",
        llm_config={"provider": "claude"},
    )

    assert "tool_choice" not in claude_adapter.configs[0]
    assert claude_adapter.configs[1]["tool_choice"] == {"type": "any"}


async def _ok():
    return {"ok": True}

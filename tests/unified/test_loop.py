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


async def _ok():
    return {"ok": True}

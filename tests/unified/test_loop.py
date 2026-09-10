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

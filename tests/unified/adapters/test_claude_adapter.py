"""ClaudeAdapter.generate -- regression coverage for a live bug: a newer
Claude model (e.g. an extended-thinking-capable one) rejects a caller-
supplied `temperature` outright with a 400 ("`temperature` is deprecated
for this model"), where older models just treat it as an optional sampling
knob. The adapter retries once without it instead of hard-failing every
request to such a model.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from anthropic import BadRequestError

from unified.adapters.base_adapter import ToolSpec
from unified.adapters.claude_adapter import ClaudeAdapter


def _adapter() -> ClaudeAdapter:
    return ClaudeAdapter(api_key="test-key")


def test_export_tools_marks_only_the_last_tool_as_a_cache_breakpoint():
    """Tool definitions never change turn to turn (unlike `system`, which
    carries TIME & LOCATION and changes almost every turn) -- they need
    their own cache_control breakpoint so a volatile system prompt doesn't
    also bust the cache for the static tool descriptions. Only the last
    tool should carry it: a single trailing breakpoint caches everything up
    to and including it, one on every tool would be wrong/wasteful."""
    specs = [
        ToolSpec(name="a", description="first", json_schema={}),
        ToolSpec(name="b", description="second", json_schema={}),
        ToolSpec(name="c", description="third", json_schema={}),
    ]
    tools = _adapter().export_tools(specs)

    assert "cache_control" not in tools[0]
    assert "cache_control" not in tools[1]
    assert tools[2]["cache_control"] == {"type": "ephemeral"}
    # Content itself must be untouched by the mutation.
    assert tools[2]["name"] == "c" and tools[2]["description"] == "third"


def test_export_tools_handles_empty_spec_list():
    assert _adapter().export_tools([]) == []


def _fake_final_message(text: str = "hi", usage: dict | None = None):
    block = SimpleNamespace(type="text", text=text, model_dump=lambda: {"type": "text", "text": text})
    dump = {"content": [{"type": "text", "text": text}]}
    if usage is not None:
        dump["usage"] = usage
    return SimpleNamespace(content=[block], model_dump=lambda: dump)


class _FakeStream:
    """Minimal stand-in for the async-context-manager `messages.stream()`
    returns -- just enough surface for ClaudeAdapter._stream_once."""

    def __init__(self, final_message):
        self._final_message = final_message

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def get_final_message(self):
        return self._final_message

    @property
    def text_stream(self):
        async def _gen():
            return
            yield  # pragma: no cover -- makes this an async generator
        return _gen()


def _temperature_deprecated_error() -> BadRequestError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(400, request=request, json={
        "type": "error",
        "error": {"type": "invalid_request_error", "message": "`temperature` is deprecated for this model."},
    })
    return BadRequestError("bad request", response=response, body=response.json())


@pytest.mark.asyncio
async def test_retries_without_temperature_on_deprecated_error():
    adapter = _adapter()
    calls = []

    def fake_stream(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _temperature_deprecated_error()
        return _FakeStream(_fake_final_message("ok"))

    adapter._client.messages.stream = fake_stream

    result = await adapter.generate(
        messages=[{"role": "user", "content": "hi"}],
        config={"temperature": 0.7},
    )

    assert len(calls) == 2
    assert calls[0]["extra_body"] == {"temperature": 0.7}
    assert "extra_body" not in calls[1]
    assert result["text"] == "ok"


@pytest.mark.asyncio
async def test_does_not_retry_an_unrelated_bad_request_error():
    adapter = _adapter()
    calls = []

    def fake_stream(**kwargs):
        calls.append(kwargs)
        request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
        response = httpx.Response(400, request=request, json={
            "type": "error",
            "error": {"type": "invalid_request_error", "message": "max_tokens is too large."},
        })
        raise BadRequestError("bad request", response=response, body=response.json())

    adapter._client.messages.stream = fake_stream

    with pytest.raises(BadRequestError):
        await adapter.generate(
            messages=[{"role": "user", "content": "hi"}],
            config={"temperature": 0.7},
        )

    assert len(calls) == 1  # not retried -- this isn't the temperature-deprecated case


@pytest.mark.asyncio
async def test_no_retry_needed_when_temperature_not_configured():
    adapter = _adapter()
    calls = []

    def fake_stream(**kwargs):
        calls.append(kwargs)
        return _FakeStream(_fake_final_message("ok"))

    adapter._client.messages.stream = fake_stream

    result = await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert len(calls) == 1
    assert "extra_body" not in calls[0]
    assert result["text"] == "ok"


@pytest.mark.asyncio
async def test_second_call_to_same_model_skips_the_failing_round_trip():
    # Once a model's rejection of `temperature` is discovered, it must be
    # remembered on the adapter instance -- every call after the first
    # should go straight through without ever attaching extra_body again.
    adapter = _adapter()
    calls = []

    def fake_stream(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            raise _temperature_deprecated_error()
        return _FakeStream(_fake_final_message("ok"))

    adapter._client.messages.stream = fake_stream

    await adapter.generate(
        messages=[{"role": "user", "content": "hi"}],
        config={"temperature": 0.7, "model": "claude-thinking-model"},
    )
    assert len(calls) == 2  # first call: fails then retries

    await adapter.generate(
        messages=[{"role": "user", "content": "hi again"}],
        config={"temperature": 0.7, "model": "claude-thinking-model"},
    )
    assert len(calls) == 3  # second call: goes straight through, no retry
    assert "extra_body" not in calls[2]


@pytest.mark.asyncio
async def test_temperature_key_present_but_none_is_not_forwarded():
    # runtime_config.py's resolve_profile always inserts a "temperature" key
    # (None when unconfigured) -- mere key presence must not be mistaken for
    # a real configured value.
    adapter = _adapter()
    calls = []

    def fake_stream(**kwargs):
        calls.append(kwargs)
        return _FakeStream(_fake_final_message("ok"))

    adapter._client.messages.stream = fake_stream

    await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={"temperature": None})

    assert "extra_body" not in calls[0]


def _capture_stream(calls: list):
    def fake_stream(**kwargs):
        calls.append(kwargs)
        return _FakeStream(_fake_final_message("ok"))
    return fake_stream


_TWO_SECTION_MESSAGES = [
    {"role": "system", "content": "static"},
    {"role": "system", "content": "tail"},
    {"role": "user", "content": "hi"},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("config", [{}, {"cache_ttl": "5m"}])
async def test_each_system_message_becomes_its_own_cached_block(config):
    # The prompt builder sends [static, volatile tail]; as one joined block
    # the tail changing every turn busted the cache for the whole system
    # prompt on every new conversation. Each section gets its own block and
    # breakpoint; 5m (the default) emits no ttl key at all.
    adapter = _adapter()
    calls: list = []
    adapter._client.messages.stream = _capture_stream(calls)

    await adapter.generate(messages=_TWO_SECTION_MESSAGES, config=config)

    assert calls[0]["system"] == [
        {"type": "text", "text": "static", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "tail", "cache_control": {"type": "ephemeral"}},
    ]


@pytest.mark.asyncio
async def test_cache_ttl_applies_to_tools_and_static_blocks_but_not_the_volatile_tail():
    # Longer TTLs must precede shorter ones in the request, so a configured
    # 1h goes on the tools and the static block(s); the tail -- which changes
    # every turn anyway -- keeps the default. The exported tools list is
    # shared across rounds and must not be mutated in place.
    adapter = _adapter()
    calls: list = []
    adapter._client.messages.stream = _capture_stream(calls)
    tools = adapter.export_tools([ToolSpec(name="a", description="d", json_schema={})])

    await adapter.generate(messages=_TWO_SECTION_MESSAGES, config={"cache_ttl": "1h"}, tools=tools)

    assert calls[0]["tools"][-1]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert tools[-1]["cache_control"] == {"type": "ephemeral"}
    assert calls[0]["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert calls[0]["system"][-1]["cache_control"] == {"type": "ephemeral"}


@pytest.mark.asyncio
async def test_only_the_last_three_system_blocks_carry_a_breakpoint():
    # 4 breakpoints max per request, one of which is the tools list.
    adapter = _adapter()
    calls: list = []
    adapter._client.messages.stream = _capture_stream(calls)
    messages = [{"role": "system", "content": f"s{i}"} for i in range(5)] + [{"role": "user", "content": "hi"}]

    await adapter.generate(messages=messages, config={})

    assert ["cache_control" in block for block in calls[0]["system"]] == [False, False, True, True, True]


@pytest.mark.asyncio
async def test_generate_surfaces_usage_including_cache_fields():
    # usage (including cache_creation_input_tokens/cache_read_input_tokens)
    # already rode along inside `raw`, but AgenticLoop's prompt-log write
    # needs it without knowing Claude's response shape -- so it must also be
    # surfaced as its own top-level key.
    adapter = _adapter()
    usage = {
        "input_tokens": 120,
        "output_tokens": 40,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 14000,
    }

    def fake_stream(**kwargs):
        return _FakeStream(_fake_final_message("ok", usage=usage))

    adapter._client.messages.stream = fake_stream

    result = await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert result["usage"] == usage
    assert result["raw"]["usage"] == usage


@pytest.mark.asyncio
async def test_generate_usage_defaults_to_empty_dict_when_absent():
    adapter = _adapter()

    def fake_stream(**kwargs):
        return _FakeStream(_fake_final_message("ok"))  # no usage= passed

    adapter._client.messages.stream = fake_stream

    result = await adapter.generate(messages=[{"role": "user", "content": "hi"}], config={})

    assert result["usage"] == {}

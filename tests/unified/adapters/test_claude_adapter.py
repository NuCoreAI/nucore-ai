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

from unified.adapters.claude_adapter import ClaudeAdapter


def _adapter() -> ClaudeAdapter:
    return ClaudeAdapter(api_key="test-key")


def _fake_final_message(text: str = "hi"):
    block = SimpleNamespace(type="text", text=text, model_dump=lambda: {"type": "text", "text": text})
    return SimpleNamespace(content=[block], model_dump=lambda: {"content": [{"type": "text", "text": text}]})


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

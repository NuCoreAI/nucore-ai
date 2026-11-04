"""IoXWrapper.notify_ai_busy -- eisy-ui's "AI busy" animation channel
(api/ai/busy), same shape/plumbing as test_app_event.py's app-event tests.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from iox.iox_wrapper import IoXWrapper


def _bare_wrapper() -> IoXWrapper:
    # Bypasses __init__ (no real hub connection needed) -- this method only
    # ever touches self.post.
    wrapper = object.__new__(IoXWrapper)
    wrapper.post_calls: list[tuple] = []

    async def fake_post(path, body, headers=None):
        wrapper.post_calls.append((path, body, headers))
        return SimpleNamespace(status_code=200)

    wrapper.post = fake_post
    return wrapper


@pytest.mark.asyncio
async def test_notify_ai_busy_enabled_posts_message():
    wrapper = _bare_wrapper()
    ok = await wrapper.notify_ai_busy(enabled=True, message="Thinking")
    assert ok is True
    assert len(wrapper.post_calls) == 1
    path, body, headers = wrapper.post_calls[0]
    assert path == "/api/ai/busy"
    assert json.loads(body) == {"enabled": True, "message": "Thinking"}
    assert headers == {"Content-Type": "application/json"}


@pytest.mark.asyncio
async def test_notify_ai_busy_disabled_omits_message():
    wrapper = _bare_wrapper()
    ok = await wrapper.notify_ai_busy(enabled=False)
    assert ok is True
    _, body, _ = wrapper.post_calls[0]
    assert json.loads(body) == {"enabled": False}


@pytest.mark.asyncio
async def test_notify_ai_busy_disabled_ignores_message_even_if_given():
    wrapper = _bare_wrapper()
    await wrapper.notify_ai_busy(enabled=False, message="Thinking")
    _, body, _ = wrapper.post_calls[0]
    assert json.loads(body) == {"enabled": False}


@pytest.mark.asyncio
async def test_notify_ai_busy_failure_response_is_reported_as_false():
    wrapper = _bare_wrapper()

    async def fake_post_failure(path, body, headers=None):
        wrapper.post_calls.append((path, body, headers))
        return SimpleNamespace(status_code=500)

    wrapper.post = fake_post_failure
    assert await wrapper.notify_ai_busy(enabled=True, message="Thinking") is False

"""IoXWrapper.dismiss_discovery_dialogs/open_qr_scan -- eisy-ui's UI-control
channel (api/app-event), distinct from the device-hardware ZMatter/Insteon
REST surface covered by test_zmatter_pairing.py.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from iox.iox_wrapper import IoXWrapper


def _bare_wrapper() -> IoXWrapper:
    # Bypasses __init__ (no real hub connection needed) -- these two methods
    # only ever touch self.post.
    wrapper = object.__new__(IoXWrapper)
    wrapper.post_calls: list[tuple] = []

    async def fake_post(path, body, headers=None):
        wrapper.post_calls.append((path, body, headers))
        return SimpleNamespace(status_code=200)

    wrapper.post = fake_post
    return wrapper


@pytest.mark.asyncio
async def test_dismiss_discovery_dialogs_posts_with_no_client_id():
    wrapper = _bare_wrapper()
    ok = await wrapper.dismiss_discovery_dialogs()
    assert ok is True
    assert len(wrapper.post_calls) == 1
    path, body, headers = wrapper.post_calls[0]
    assert path == "/api/app-event"
    assert json.loads(body) == {"action": "dismissDiscoveryDialogs"}
    assert headers == {"Content-Type": "application/json"}


@pytest.mark.asyncio
async def test_open_qr_scan_posts_with_client_id_and_no_action_data_when_raw_omitted():
    wrapper = _bare_wrapper()
    ok = await wrapper.open_qr_scan("client-1")
    assert ok is True
    path, body, headers = wrapper.post_calls[0]
    assert path == "/api/app-event"
    assert json.loads(body) == {"action": "openQrScan", "clientId": "client-1"}


@pytest.mark.asyncio
async def test_open_qr_scan_posts_action_data_raw_when_given():
    wrapper = _bare_wrapper()
    ok = await wrapper.open_qr_scan("client-1", raw="0123-4567")
    assert ok is True
    path, body, headers = wrapper.post_calls[0]
    assert json.loads(body) == {
        "action": "openQrScan",
        "clientId": "client-1",
        "actionData": {"raw": "0123-4567"},
    }


@pytest.mark.asyncio
async def test_app_event_failure_response_is_reported_as_false():
    wrapper = _bare_wrapper()

    async def fake_post_failure(path, body, headers=None):
        wrapper.post_calls.append((path, body, headers))
        return SimpleNamespace(status_code=500)

    wrapper.post = fake_post_failure
    assert await wrapper.dismiss_discovery_dialogs() is False
    assert await wrapper.open_qr_scan("client-1") is False

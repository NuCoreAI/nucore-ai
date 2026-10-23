"""Verifies IoXWrapper.notify_plugin_files_changed: POST /api/plugin/code/
files-changed with {"paths": [...]}. Covers: happy path, non-200, no
response (connection failure), and an exception while parsing the response
-- all {"error": ...} on failure, never raised.
"""

from __future__ import annotations

import json

import pytest

from iox.iox_wrapper import IoXWrapper


class FakeResp:
    def __init__(self, status_code=200, data=None, json_raises=None):
        self.status_code = status_code
        self._data = data
        self._json_raises = json_raises

    def json(self):
        if self._json_raises is not None:
            raise self._json_raises
        return self._data


def _bare_wrapper() -> IoXWrapper:
    return object.__new__(IoXWrapper)


@pytest.mark.asyncio
async def test_notify_plugin_files_changed_posts_expected_path_and_body():
    wrapper = _bare_wrapper()
    calls = []

    async def fake_post(path, body=None, headers=None):
        calls.append((path, body, headers))
        return FakeResp(data={"successful": True})

    wrapper.post = fake_post

    result = await wrapper.notify_plugin_files_changed(["/abs/plugin.py", "/abs/main.py"])

    assert calls[0][0] == "/api/plugin/code/files-changed"
    assert json.loads(calls[0][1]) == {"paths": ["/abs/plugin.py", "/abs/main.py"]}
    assert calls[0][2] == {"Content-Type": "application/json"}
    assert result == {"successful": True}


@pytest.mark.asyncio
async def test_notify_plugin_files_changed_returns_error_on_non_200():
    wrapper = _bare_wrapper()

    async def fake_post(path, body=None, headers=None):
        return FakeResp(status_code=500)

    wrapper.post = fake_post

    result = await wrapper.notify_plugin_files_changed(["/abs/plugin.py"])

    assert "error" in result


@pytest.mark.asyncio
async def test_notify_plugin_files_changed_returns_error_on_no_response():
    wrapper = _bare_wrapper()

    async def fake_post(path, body=None, headers=None):
        return None

    wrapper.post = fake_post

    result = await wrapper.notify_plugin_files_changed(["/abs/plugin.py"])

    assert "error" in result


@pytest.mark.asyncio
async def test_notify_plugin_files_changed_returns_error_on_exception():
    wrapper = _bare_wrapper()

    async def fake_post(path, body=None, headers=None):
        return FakeResp(json_raises=ValueError("bad json"))

    wrapper.post = fake_post

    result = await wrapper.notify_plugin_files_changed(["/abs/plugin.py"])

    assert "error" in result

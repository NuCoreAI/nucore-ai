"""Verifies IoXWrapper's plugin-management REST support: GET /api/plugins
(get_installed_plugins) and POST /api/plugins/<profileNum>/{start,stop,restart}
(plugin_ops).
"""

from __future__ import annotations

import json

import pytest

from iox.iox_wrapper import IoXWrapper


class FakeResp:
    def __init__(self, status_code=200, data=None):
        self.status_code = status_code
        self._data = data

    def json(self):
        return self._data


def _bare_wrapper() -> IoXWrapper:
    return object.__new__(IoXWrapper)


@pytest.mark.asyncio
async def test_get_installed_plugins_hits_get_endpoint():
    wrapper = _bare_wrapper()
    calls = []
    payload = {"successful": True, "data": [{"profileNum": 3, "name": "YouTube", "isLocal": False}]}
    wrapper.get = lambda path: (calls.append(path), FakeResp(data=payload))[1]

    result = await wrapper.get_installed_plugins()

    assert calls == ["/api/plugins"]
    assert result == payload


@pytest.mark.asyncio
async def test_get_installed_plugins_returns_none_on_connection_error():
    wrapper = _bare_wrapper()
    wrapper.get = lambda path: None

    assert await wrapper.get_installed_plugins() is None


@pytest.mark.parametrize("operation", ["start", "stop", "restart"])
@pytest.mark.asyncio
async def test_plugin_ops_hits_the_right_endpoint(operation):
    wrapper = _bare_wrapper()
    calls = []
    wrapper.post = lambda path, body=None, headers=None: (calls.append(path), FakeResp(data={"successful": True}))[1]

    result = await wrapper.plugin_ops("3", operation)

    assert calls == [f"/api/plugins/3/{operation}"]
    assert result == {"successful": True}


@pytest.mark.asyncio
async def test_plugin_ops_returns_response_on_non_200():
    wrapper = _bare_wrapper()
    wrapper.post = lambda path, body=None, headers=None: FakeResp(status_code=500)

    result = await wrapper.plugin_ops("3", "start")

    assert result.status_code == 500


@pytest.mark.asyncio
async def test_plugin_ops_unimplemented_operation_raises():
    wrapper = _bare_wrapper()

    with pytest.raises(NotImplementedError):
        await wrapper.plugin_ops("3", "install")

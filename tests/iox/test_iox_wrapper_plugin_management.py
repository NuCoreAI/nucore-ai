"""IoXWrapper.register_local_plugin/install_local_plugin (new, Phase 4 of
design/developers/plugin_authoring_p4_impl.md) and the configure_plugin fix
(was `raise NotImplementedError`, now a real call) -- exact path/body shape
per design/developers/plugin-api.md.
"""

from __future__ import annotations

import json

import pytest

from iox.iox_wrapper import IoXWrapper


class FakeResp:
    def __init__(self, status_code=200, json_data=None):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {}

    def json(self):
        return self._json_data


def _bare_wrapper() -> IoXWrapper:
    return object.__new__(IoXWrapper)


# --- register_local_plugin ---


@pytest.mark.asyncio
async def test_register_local_plugin_puts_the_entry_body():
    wrapper = _bare_wrapper()
    calls = []

    async def fake_put(path, body, headers):
        calls.append((path, body, headers))
        return FakeResp(json_data={"successful": True, "data": {}})

    wrapper.put = fake_put

    entry = {"name": "AcmePool", "type": "python3", "path": "/x", "executable": "main.py", "runAs": "eisyai"}
    result = await wrapper.register_local_plugin(entry)

    assert result == {"successful": True, "data": {}}
    (path, body, headers) = calls[0]
    assert path == "/api/plugins/store/local/entry"
    assert json.loads(body) == entry
    assert headers == {"Content-Type": "application/json"}


@pytest.mark.asyncio
async def test_register_local_plugin_returns_none_on_connection_failure():
    wrapper = _bare_wrapper()

    async def fake_put(path, body, headers):
        return None

    wrapper.put = fake_put

    assert await wrapper.register_local_plugin({"name": "x"}) is None


@pytest.mark.asyncio
async def test_register_local_plugin_returns_response_on_non_200():
    wrapper = _bare_wrapper()
    resp = FakeResp(status_code=400)

    async def fake_put(path, body, headers):
        return resp

    wrapper.put = fake_put

    assert await wrapper.register_local_plugin({"name": "x"}) is resp


# --- install_local_plugin ---


@pytest.mark.asyncio
async def test_install_local_plugin_posts_nsid_only_when_no_profile_num():
    wrapper = _bare_wrapper()
    calls = []

    async def fake_post(path, body, headers=None):
        calls.append((path, body, headers))
        return FakeResp(json_data={"successful": True, "data": {"profileNum": 7}})

    wrapper.post = fake_post

    result = await wrapper.install_local_plugin("local.acme-pool")

    assert result == {"successful": True, "data": {"profileNum": 7}}
    (path, body, headers) = calls[0]
    assert path == "/api/plugins/store/local/install"
    assert json.loads(body) == {"nsid": "local.acme-pool"}


@pytest.mark.asyncio
async def test_install_local_plugin_includes_profile_num_when_given():
    wrapper = _bare_wrapper()
    calls = []

    async def fake_post(path, body, headers=None):
        calls.append((path, body, headers))
        return FakeResp(json_data={"successful": True, "data": {"profileNum": 3}})

    wrapper.post = fake_post

    await wrapper.install_local_plugin("local.acme-pool", profile_num=3)

    (_, body, _) = calls[0]
    assert json.loads(body) == {"nsid": "local.acme-pool", "profileNum": 3}


# --- configure_plugin (the fix) ---


@pytest.mark.asyncio
async def test_configure_plugin_defaults_to_customparams_key():
    wrapper = _bare_wrapper()
    calls = []

    async def fake_post(path, body, headers=None):
        calls.append((path, body, headers))
        return FakeResp(json_data={"successful": True, "data": {}})

    wrapper.post = fake_post

    result = await wrapper.configure_plugin("7", {"api_key": "placeholder"})

    assert result == {"successful": True, "data": {}}
    (path, body, _) = calls[0]
    assert path == "/api/plugin/7/custom/customparams"
    assert json.loads(body) == {"api_key": "placeholder"}


@pytest.mark.asyncio
async def test_configure_plugin_accepts_an_oauth_key():
    wrapper = _bare_wrapper()
    calls = []

    async def fake_post(path, body, headers=None):
        calls.append((path, body, headers))
        return FakeResp(json_data={"successful": True, "data": {}})

    wrapper.post = fake_post

    await wrapper.configure_plugin("7", {"client_id": "real-id"}, key="oauth")

    (path, body, _) = calls[0]
    assert path == "/api/plugin/7/custom/oauth"
    assert json.loads(body) == {"client_id": "real-id"}


@pytest.mark.asyncio
async def test_configure_plugin_returns_none_on_connection_failure():
    wrapper = _bare_wrapper()

    async def fake_post(path, body, headers=None):
        return None

    wrapper.post = fake_post

    assert await wrapper.configure_plugin("7", {}) is None

"""IoXWrapper.setup_eisy_ai_vscode_remote -- the single-request POST to the
eisy-ai VS Code remote-development setup service. Covers: happy path,
non-200 response, no response (connection failure), and an exception while
parsing the response -- all {"error": ...} on failure, never raised.
"""

from __future__ import annotations

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


def _bare_wrapper(response) -> IoXWrapper:
    wrapper = object.__new__(IoXWrapper)
    wrapper.requested_calls = []

    async def fake_post(path, body, headers=None):
        wrapper.requested_calls.append((path, body))
        return response

    wrapper.post = fake_post
    return wrapper


@pytest.mark.asyncio
async def test_setup_eisy_ai_vscode_remote_posts_expected_path_and_body():
    wrapper = _bare_wrapper(FakeResp(data={"successful": True}))

    result = await wrapper.setup_eisy_ai_vscode_remote()

    assert result == {"successful": True}
    assert wrapper.requested_calls == [("/api/udx/rest/udx.sys.ops/eisyai.ops/setup_vscode_remote", "e=mc2")]


@pytest.mark.asyncio
async def test_setup_eisy_ai_vscode_remote_returns_error_on_non_200():
    wrapper = _bare_wrapper(FakeResp(status_code=500))

    result = await wrapper.setup_eisy_ai_vscode_remote()

    assert "error" in result


@pytest.mark.asyncio
async def test_setup_eisy_ai_vscode_remote_returns_error_on_no_response():
    wrapper = _bare_wrapper(None)

    result = await wrapper.setup_eisy_ai_vscode_remote()

    assert "error" in result


@pytest.mark.asyncio
async def test_setup_eisy_ai_vscode_remote_returns_error_on_exception():
    wrapper = _bare_wrapper(FakeResp(json_raises=ValueError("bad json")))

    result = await wrapper.setup_eisy_ai_vscode_remote()

    assert "error" in result

"""IoXDiagnostics.refresh_eisy_ai_permissions -- the single-request POST to
the eisy-ai permissions-refresh service that plugin-authoring call sites
trigger once after writing a batch of files. Covers: happy path, non-200
response, no response (connection failure), and an exception while parsing
the response -- all {"error": ...} on failure, never raised.
"""

from __future__ import annotations

import pytest

from iox.diagnostics.iox_diagnostics import IoXDiagnostics


class FakeResp:
    def __init__(self, status_code=200, data=None, json_raises=None):
        self.status_code = status_code
        self._data = data
        self._json_raises = json_raises

    def json(self):
        if self._json_raises is not None:
            raise self._json_raises
        return self._data


class FakeWrapper:
    def __init__(self, response):
        self._response = response
        self.requested_calls = []

    async def post(self, path, body):
        self.requested_calls.append((path, body))
        return self._response


@pytest.mark.asyncio
async def test_refresh_eisy_ai_permissions_posts_expected_path_and_body():
    wrapper = FakeWrapper(FakeResp(data={"successful": True}))
    diagnostics = IoXDiagnostics(wrapper)

    result = await diagnostics.refresh_eisy_ai_permissions()

    assert result == {"successful": True}
    assert wrapper.requested_calls == [("/api/udx/rest/udx.sys.ops/eisyai.ops/refresh_permissions", "e=mc2")]


@pytest.mark.asyncio
async def test_refresh_eisy_ai_permissions_returns_error_on_non_200():
    wrapper = FakeWrapper(FakeResp(status_code=500))
    diagnostics = IoXDiagnostics(wrapper)

    result = await diagnostics.refresh_eisy_ai_permissions()

    assert "error" in result


@pytest.mark.asyncio
async def test_refresh_eisy_ai_permissions_returns_error_on_no_response():
    wrapper = FakeWrapper(None)
    diagnostics = IoXDiagnostics(wrapper)

    result = await diagnostics.refresh_eisy_ai_permissions()

    assert "error" in result


@pytest.mark.asyncio
async def test_refresh_eisy_ai_permissions_returns_error_on_exception():
    wrapper = FakeWrapper(FakeResp(json_raises=ValueError("bad json")))
    diagnostics = IoXDiagnostics(wrapper)

    result = await diagnostics.refresh_eisy_ai_permissions()

    assert "error" in result

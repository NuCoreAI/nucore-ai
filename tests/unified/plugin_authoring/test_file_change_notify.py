"""file_change_notify.notify_files_changed -- the shared post-write hook
that calls NuCoreInterface.notify_plugin_files_changed once per batch.
Covers: empty-batch no-op, {"error": ...} passthrough, an unexpected
non-dict response, and the success path.
"""

from __future__ import annotations

import pytest

from unified.plugin_authoring.file_change_notify import notify_files_changed


class _Unreachable:
    async def notify_plugin_files_changed(self, paths):
        raise AssertionError("should not be called for an empty paths list")


class _FakeNuCoreInterface:
    def __init__(self, response):
        self._response = response
        self.received_paths = None

    async def notify_plugin_files_changed(self, paths):
        self.received_paths = paths
        return self._response


@pytest.mark.asyncio
async def test_empty_paths_is_a_noop():
    assert await notify_files_changed(_Unreachable(), []) is None


@pytest.mark.asyncio
async def test_error_response_is_passed_through():
    fake = _FakeNuCoreInterface({"error": "boom"})

    result = await notify_files_changed(fake, ["/abs/plugin.py"])

    assert result == "boom"
    assert fake.received_paths == ["/abs/plugin.py"]


@pytest.mark.asyncio
async def test_unexpected_non_dict_response_is_reported():
    fake = _FakeNuCoreInterface(None)

    result = await notify_files_changed(fake, ["/abs/plugin.py"])

    assert result is not None
    assert "unexpected" in result


@pytest.mark.asyncio
async def test_success_returns_none():
    fake = _FakeNuCoreInterface({"successful": True})

    result = await notify_files_changed(fake, ["/abs/plugin.py"])

    assert result is None

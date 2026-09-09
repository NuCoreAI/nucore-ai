"""IoXWrapper.discover_devices/finish_device_discovery -- the
Z-Matter-generation Z-Wave/Zigbee/Matter pairing path (bare REST GETs under
ZMATTER_BASE_PATHS, no SOAP translation, no request body), and the Legacy
Z-Wave carve-out: _is_legacy_zwave() makes the backend raise NuCoreError
instead of silently hitting the wrong endpoint shape for a controller that
hasn't been upgraded to Z-Matter yet. Also confirms the pre-existing Insteon
POST path (start-linking/stop-linking) is unaffected when no protocol (or
protocol="insteon") is passed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from iox.iox_wrapper import IoXWrapper
from nucore.nucore_error import NuCoreError

_ZMATTER_PROTOCOLS = [
    ("zwave", "/rest/zmatter/zwave/"),
    ("zigbee", "/rest/zmatter/zigbee/"),
    ("matter", "/rest/zmatter/matter/"),
]


class _FakeDiagnostics:
    def __init__(self, zmatter_zwave: bool):
        self._zmatter_zwave = zmatter_zwave

    async def _get_system_options(self):
        return {"ZMatterZWave": self._zmatter_zwave}


def _bare_wrapper(zmatter_zwave: bool = True) -> IoXWrapper:
    # Bypasses __init__ (no real hub connection needed) -- discover_devices/
    # finish_device_discovery/_is_legacy_zwave only ever touch self.diagnostics,
    # self.get, self.post, and the class-level _EISYUI_INSTANCE constant.
    wrapper = object.__new__(IoXWrapper)
    wrapper.diagnostics = _FakeDiagnostics(zmatter_zwave)
    wrapper.get_calls: list[str] = []
    wrapper.post_calls: list[tuple] = []

    def fake_get(path):
        wrapper.get_calls.append(path)
        return SimpleNamespace(status_code=200)

    def fake_post(path, body, headers=None):
        wrapper.post_calls.append((path, body, headers))
        return SimpleNamespace(status_code=200)

    wrapper.get = fake_get
    wrapper.post = fake_post
    return wrapper


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,base", _ZMATTER_PROTOCOLS)
async def test_discover_devices_include_hits_the_right_zmatter_url(protocol, base):
    wrapper = _bare_wrapper()
    ok = await wrapper.discover_devices(protocol=protocol, mode="include")
    assert ok is True
    assert wrapper.get_calls == [f"{base}node/include"]
    assert wrapper.post_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,base", _ZMATTER_PROTOCOLS)
async def test_discover_devices_exclude_hits_the_right_zmatter_url(protocol, base):
    wrapper = _bare_wrapper()
    ok = await wrapper.discover_devices(protocol=protocol, mode="exclude")
    assert ok is True
    assert wrapper.get_calls == [f"{base}node/exclude"]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,base", _ZMATTER_PROTOCOLS)
async def test_finish_device_discovery_hits_cancel_regardless_of_mode(protocol, base):
    # cancel stops whichever mode (include or exclude) is active -- there's
    # only ever one "finish" endpoint per protocol, unlike discover_devices.
    wrapper = _bare_wrapper()
    ok = await wrapper.finish_device_discovery(protocol=protocol)
    assert ok is True
    assert wrapper.get_calls == [f"{base}node/cancel"]


@pytest.mark.asyncio
async def test_discover_devices_falls_back_to_insteon_post_when_no_protocol():
    wrapper = _bare_wrapper()
    ok = await wrapper.discover_devices()
    assert ok is True
    assert wrapper.get_calls == []
    assert len(wrapper.post_calls) == 1
    assert wrapper.post_calls[0][0] == "/api/family/1/1/start-linking"


@pytest.mark.asyncio
async def test_finish_device_discovery_falls_back_to_insteon_post_when_no_protocol():
    wrapper = _bare_wrapper()
    ok = await wrapper.finish_device_discovery(flag=3)
    assert ok is True
    assert wrapper.get_calls == []
    assert len(wrapper.post_calls) == 1
    assert wrapper.post_calls[0][0] == "/api/family/1/1/stop-linking"


@pytest.mark.asyncio
async def test_legacy_zwave_raises_instead_of_hitting_the_zmatter_url():
    wrapper = _bare_wrapper(zmatter_zwave=False)
    with pytest.raises(NuCoreError, match="Legacy Z-Wave"):
        await wrapper.discover_devices(protocol="zwave", mode="include")
    assert wrapper.get_calls == []


@pytest.mark.asyncio
async def test_legacy_zwave_finish_raises_instead_of_hitting_the_zmatter_url():
    wrapper = _bare_wrapper(zmatter_zwave=False)
    with pytest.raises(NuCoreError, match="Legacy Z-Wave"):
        await wrapper.finish_device_discovery(protocol="zwave")
    assert wrapper.get_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol,base", [("zigbee", "/rest/zmatter/zigbee/"), ("matter", "/rest/zmatter/matter/")])
async def test_legacy_zwave_check_does_not_apply_to_zigbee_or_matter(protocol, base):
    # Only Z-Wave has a Legacy (pre-Z-Matter) generation among these family
    # IDs -- Zigbee/Matter are always Z-Matter-shaped, so a controller still
    # on Legacy Z-Wave must not block zigbee/matter pairing too.
    wrapper = _bare_wrapper(zmatter_zwave=False)
    ok = await wrapper.discover_devices(protocol=protocol, mode="include")
    assert ok is True
    assert wrapper.get_calls == [f"{base}node/include"]

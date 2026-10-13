"""IoXDiagnostics.get_device_mac_address -- the single-request path to the
hub's own MAC (via /desc's UPnP UDN field) that generate_plugin_scaffold's
guard 4a uses to build globally-unique nodedef ids. Covers: happy path,
non-200 response, and malformed XML -- all None-on-failure, never raised.
"""

from __future__ import annotations

import pytest

from iox.diagnostics.iox_diagnostics import IoXDiagnostics


class FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class FakeWrapper:
    def __init__(self, response):
        self._response = response
        self.requested_paths = []

    async def get(self, path):
        self.requested_paths.append(path)
        return self._response


_DESC_XML = """<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <friendlyName>Test ISY</friendlyName>
    <UDN>uuid:AA:BB:CC:DD:EE:FF</UDN>
  </device>
</root>"""


@pytest.mark.asyncio
async def test_get_device_mac_address_strips_uuid_prefix():
    wrapper = FakeWrapper(FakeResp(text=_DESC_XML))
    diagnostics = IoXDiagnostics(wrapper)

    mac = await diagnostics.get_device_mac_address()

    assert mac == "AA:BB:CC:DD:EE:FF"
    assert wrapper.requested_paths == ["/desc"]


@pytest.mark.asyncio
async def test_get_device_mac_address_returns_none_on_non_200():
    wrapper = FakeWrapper(FakeResp(status_code=500, text=""))
    diagnostics = IoXDiagnostics(wrapper)

    assert await diagnostics.get_device_mac_address() is None


@pytest.mark.asyncio
async def test_get_device_mac_address_returns_none_on_no_response():
    wrapper = FakeWrapper(None)
    diagnostics = IoXDiagnostics(wrapper)

    assert await diagnostics.get_device_mac_address() is None


@pytest.mark.asyncio
async def test_get_device_mac_address_returns_none_on_malformed_xml():
    wrapper = FakeWrapper(FakeResp(text="<not><valid"))
    diagnostics = IoXDiagnostics(wrapper)

    assert await diagnostics.get_device_mac_address() is None


@pytest.mark.asyncio
async def test_get_device_mac_address_returns_none_when_udn_missing():
    desc_without_udn = """<root xmlns="urn:schemas-upnp-org:device-1-0">
  <device>
    <friendlyName>Test ISY</friendlyName>
  </device>
</root>"""
    wrapper = FakeWrapper(FakeResp(text=desc_without_udn))
    diagnostics = IoXDiagnostics(wrapper)

    assert await diagnostics.get_device_mac_address() is None

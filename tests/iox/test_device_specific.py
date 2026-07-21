"""IoXWrapper.send_device_specific/send_device_specific_with_option -- Python
port of the Java SDK's two sendDeviceSpecific() overloads, built on
soap_post(). Covers: envelope/SOAPAction shape, XML-escaping of plain
parameters vs. raw passthrough for the CDATA/specs document, the flag's
ordinal-value conversion (matching the Java `new Integer(char)` widening
quirk), and the None-on-failure paths.
"""

from __future__ import annotations

import pytest

from iox.iox_wrapper import IoXWrapper


class FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def _bare_wrapper() -> IoXWrapper:
    return object.__new__(IoXWrapper)


@pytest.mark.asyncio
async def test_send_device_specific_builds_expected_envelope_and_action():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.soap_post = lambda path, body, soap_action=None, headers=None: (
        calls.append((path, body, soap_action)),
        FakeResp(text="<result/>"),
    )[1]

    result = await wrapper.send_device_specific("STATUS", "n001", "a", "b", "c", specs="<doc/>")

    assert result == "<result/>"
    (path, body, action) = calls[0]
    assert path == "/services"
    assert action == "DeviceSpecific"
    assert body == (
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Body>"
        '<u:DeviceSpecific xmlns:u="urn:udi-com:service:X_Insteon_Lighting_Service:1">'
        "<command>STATUS</command><node>n001</node>"
        "<p1>a</p1><p2>b</p2><p3>c</p3><flag>0</flag><CDATA><doc/></CDATA>"
        "</u:DeviceSpecific></s:Body></s:Envelope>"
    )


@pytest.mark.asyncio
async def test_send_device_specific_escapes_plain_params_but_not_specs():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.soap_post = lambda path, body, soap_action=None, headers=None: (
        calls.append(body),
        FakeResp(),
    )[1]

    await wrapper.send_device_specific("A & B", "n<1>", specs="<raw>&unescaped</raw>")

    body = calls[0]
    assert "<command>A &amp; B</command>" in body
    assert "<node>n&lt;1&gt;</node>" in body
    # specs/CDATA is passed through raw -- it's meant to carry an XML document.
    assert "<CDATA><raw>&unescaped</raw></CDATA>" in body


@pytest.mark.asyncio
async def test_send_device_specific_omitted_params_become_empty_elements():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.soap_post = lambda path, body, soap_action=None, headers=None: (
        calls.append(body),
        FakeResp(),
    )[1]

    await wrapper.send_device_specific("STATUS", "n001")

    body = calls[0]
    assert "<p1></p1><p2></p2><p3></p3>" in body
    assert "<CDATA></CDATA>" in body


@pytest.mark.asyncio
async def test_send_device_specific_returns_none_on_non_200():
    wrapper = _bare_wrapper()
    wrapper.soap_post = lambda *a, **kw: FakeResp(status_code=500)

    assert await wrapper.send_device_specific("STATUS", "n001") is None


@pytest.mark.asyncio
async def test_send_device_specific_returns_none_on_connection_error():
    wrapper = _bare_wrapper()
    wrapper.soap_post = lambda *a, **kw: None

    assert await wrapper.send_device_specific("STATUS", "n001") is None


@pytest.mark.asyncio
async def test_send_device_specific_with_option_uses_option_tag():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.soap_post = lambda path, body, soap_action=None, headers=None: (
        calls.append(body),
        FakeResp(),
    )[1]

    await wrapper.send_device_specific_with_option("STATUS", "n001", option="fast")

    body = calls[0]
    assert "<option>fast</option>" in body
    assert "<p1>" not in body


@pytest.mark.asyncio
async def test_send_device_specific_with_option_sends_flag_value_as_given():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.soap_post = lambda path, body, soap_action=None, headers=None: (
        calls.append(body),
        FakeResp(),
    )[1]

    await wrapper.send_device_specific_with_option("STATUS", "n001", flag="200")

    assert "<flag>200</flag>" in calls[0]


@pytest.mark.asyncio
async def test_send_device_specific_with_option_defaults_flag_to_zero():
    wrapper = _bare_wrapper()
    calls = []
    wrapper.soap_post = lambda path, body, soap_action=None, headers=None: (
        calls.append(body),
        FakeResp(),
    )[1]

    await wrapper.send_device_specific_with_option("STATUS", "n001")

    assert "<flag>0</flag>" in calls[0]

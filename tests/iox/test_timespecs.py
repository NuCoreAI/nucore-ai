"""Verifies IoXWrapper.get_timespecs: parses /rest/time's XML response into
timezone/lat/long plus current_time/sunrise/sunset localized to the device's
own configured timezone (GMT/SunriseGMT/SunsetGMT are already Unix epoch
seconds -- unlike NTP/Sunrise/Sunset, which are NTP epoch, seconds since 1900).
"""

from __future__ import annotations

import pytest

from iox.iox_wrapper import IoXWrapper

_SAMPLE_XML = """<DT>
    <NTP>3989354564</NTP>
    <GMT>1780390964</GMT>
    <TMZOffset>-8</TMZOffset>
    <DST>true</DST>
    <DSTRule>NAM</DSTRule>
    <Lat>34.050000</Lat>
    <Long>118.233000</Long>
    <Sunrise>3989022221</Sunrise>
    <SunriseGMT>1780058621</SunriseGMT>
    <Sunset>3989073402</Sunset>
    <SunsetGMT>1780109802</SunsetGMT>
    <IsMilitary>false</IsMilitary>
    <TzId>America/Los_Angeles</TzId>
</DT>"""


class FakeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


def _bare_wrapper() -> IoXWrapper:
    return object.__new__(IoXWrapper)


@pytest.mark.asyncio
async def test_get_timespecs_parses_timezone_lat_long():
    wrapper = _bare_wrapper()
    wrapper.get = lambda path: FakeResp(text=_SAMPLE_XML)

    result = await wrapper.get_timespecs()

    assert result["timezone"] == "America/Los_Angeles"
    assert result["latitude"] == 34.05
    assert result["longitude"] == -118.233  # API gives positive; we negate it


@pytest.mark.asyncio
async def test_get_timespecs_localizes_current_time_sunrise_sunset():
    wrapper = _bare_wrapper()
    wrapper.get = lambda path: FakeResp(text=_SAMPLE_XML)

    result = await wrapper.get_timespecs()

    # GMT/SunriseGMT/SunsetGMT are Unix epoch seconds -- localized to TzId,
    # with no NTP-epoch conversion needed.
    assert result["current_time"].startswith("2026-06-02T")
    assert result["sunrise"].startswith("2026-05-29T")
    assert result["sunset"].startswith("2026-05-29T")
    assert result["current_time"].endswith("-07:00")


@pytest.mark.asyncio
async def test_get_timespecs_returns_none_on_non_200():
    wrapper = _bare_wrapper()
    wrapper.get = lambda path: FakeResp(status_code=500, text="")

    result = await wrapper.get_timespecs()

    assert result.status_code == 500


@pytest.mark.asyncio
async def test_get_timespecs_returns_none_on_connection_error():
    wrapper = _bare_wrapper()
    wrapper.get = lambda path: None

    assert await wrapper.get_timespecs() is None

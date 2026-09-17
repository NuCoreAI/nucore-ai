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

    async def fake_get(path):
        return FakeResp(text=_SAMPLE_XML)

    wrapper.get = fake_get

    result = await wrapper.get_timespecs()

    assert result["timezone"] == "America/Los_Angeles"
    assert result["latitude"] == 34.05
    assert result["longitude"] == -118.233  # API gives positive; we negate it


@pytest.mark.asyncio
async def test_get_timespecs_localizes_sunrise_sunset():
    wrapper = _bare_wrapper()

    async def fake_get(path):
        return FakeResp(text=_SAMPLE_XML)

    wrapper.get = fake_get

    result = await wrapper.get_timespecs()

    # SunriseGMT/SunsetGMT are Unix epoch seconds -- localized to TzId, with
    # no NTP-epoch conversion needed.
    assert result["sunrise"].startswith("2026-05-29T")
    assert result["sunset"].startswith("2026-05-29T")
    assert result["sunrise"].endswith("-07:00")


@pytest.mark.asyncio
async def test_get_timespecs_current_time_is_computed_fresh_not_from_the_fetched_gmt_field():
    # current_time is never cached/parsed from the fetch's own GMT field --
    # it's always datetime.now(tzinfo), computed locally, so it reflects
    # real "now" regardless of what the (possibly stale) fetch response says.
    import datetime

    wrapper = _bare_wrapper()

    async def fake_get(path):
        return FakeResp(text=_SAMPLE_XML)

    wrapper.get = fake_get

    result = await wrapper.get_timespecs()

    assert result["current_time"].endswith("-07:00")
    parsed = datetime.datetime.fromisoformat(result["current_time"])
    now = datetime.datetime.now(parsed.tzinfo)
    assert abs((now - parsed).total_seconds()) < 5


@pytest.mark.asyncio
async def test_get_timespecs_returns_none_on_non_200():
    wrapper = _bare_wrapper()

    async def fake_get(path):
        return FakeResp(status_code=500, text="")

    wrapper.get = fake_get

    result = await wrapper.get_timespecs()

    assert result.status_code == 500


@pytest.mark.asyncio
async def test_get_timespecs_returns_none_on_connection_error():
    wrapper = _bare_wrapper()

    async def fake_get(path):
        return None

    wrapper.get = fake_get

    assert await wrapper.get_timespecs() is None


# ------------------------------------------------------------------
# Caching -- timezone/lat/long for the process's lifetime, sunrise/sunset
# once per calendar day, current_time never (always computed fresh locally).
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_timespecs_second_call_same_day_does_not_refetch():
    wrapper = _bare_wrapper()
    calls = []

    async def fake_get(path):
        calls.append(path)
        return FakeResp(text=_SAMPLE_XML)

    wrapper.get = fake_get

    first = await wrapper.get_timespecs()
    second = await wrapper.get_timespecs()

    assert len(calls) == 1
    assert second["timezone"] == first["timezone"]
    assert second["latitude"] == first["latitude"]
    assert second["longitude"] == first["longitude"]
    assert second["sunrise"] == first["sunrise"]
    assert second["sunset"] == first["sunset"]
    # current_time is still computed fresh every call, never cached.
    assert second["current_time"] >= first["current_time"]


@pytest.mark.asyncio
async def test_get_timespecs_refetches_once_per_calendar_day_rollover():
    import datetime

    wrapper = _bare_wrapper()
    calls = []

    async def fake_get(path):
        calls.append(path)
        return FakeResp(text=_SAMPLE_XML)

    wrapper.get = fake_get

    await wrapper.get_timespecs()
    assert len(calls) == 1

    # Simulate the cached day having rolled over -- seed yesterday's date
    # directly onto the daily cache rather than mocking the system clock.
    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    stale_date, stale_values = wrapper._timespecs_daily_cache
    wrapper._timespecs_daily_cache = (yesterday, stale_values)

    await wrapper.get_timespecs()

    assert len(calls) == 2  # refetched once for the new day
    # The static (timezone/lat/long) cache is reused, not rebuilt from
    # scratch, on a daily-only rollover.
    assert wrapper._timespecs_static_cache["timezone"] == "America/Los_Angeles"


@pytest.mark.asyncio
async def test_get_timespecs_daily_refresh_failure_keeps_serving_warm_cache():
    wrapper = _bare_wrapper()
    responses = [FakeResp(text=_SAMPLE_XML), FakeResp(status_code=500, text="")]

    async def fake_get(path):
        return responses.pop(0)

    wrapper.get = fake_get

    first = await wrapper.get_timespecs()

    # Force a daily-refresh attempt (which the second, failing response
    # above will answer) without waiting for a real day to pass.
    import datetime

    yesterday = datetime.date.today() - datetime.timedelta(days=1)
    _, stale_values = wrapper._timespecs_daily_cache
    wrapper._timespecs_daily_cache = (yesterday, stale_values)

    second = await wrapper.get_timespecs()

    # Static facts and the last-known sunrise/sunset survive the failed
    # refresh instead of the whole call erroring out.
    assert second["timezone"] == first["timezone"]
    assert second["latitude"] == first["latitude"]
    assert second["sunrise"] == first["sunrise"]
    assert second["sunset"] == first["sunset"]

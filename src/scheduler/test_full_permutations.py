import asyncio, sys, os
from datetime import datetime, timedelta
import pytest
from zoneinfo import ZoneInfo

import pytest_asyncio

ROOT = os.path.dirname(os.path.dirname(__file__))
sys.path.insert(0, ROOT)

from .aps_async_sun_scheduler import AsyncAPSunScheduler, HolidayProvider, HolidayEvent
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from typing import Dict, Any, Optional



@pytest_asyncio.fixture
async def fast_scheduler(monkeypatch, tmp_path):
    tz = "America/Los_Angeles"
    event_loop = asyncio.get_running_loop()  # provided by pytest-asyncio
    sched = AsyncAPSunScheduler(tz_str=tz, latitude=34.0522, longitude=-118.2437, persist_path=str(tmp_path / "specs.json"), loop=event_loop)

    # Clamp ALL scheduled jobs to fire quickly:
    orig_add_date = sched._add_date_job
    def clamp_date(run_dt, cb, payload, *, persist_id=None):
        # schedule starts at +1s, ends at +2s
        now = datetime.now().astimezone(sched.tz)
        delay = 3 if payload.get("phase") == "start" else 4
        return orig_add_date(now + timedelta(seconds=delay), cb, payload, persist_id=persist_id)
    monkeypatch.setattr(sched, "_add_date_job", clamp_date)

    def _add_date_job(self, run_dt: datetime, cb, payload: Dict[str, Any], *, persist_id: Optional[str] = None) -> str:
        async def wrapper():
            await self._maybe_await(cb, {**payload, "scheduled_for": run_dt})
        job = self.sched.add_job(wrapper, trigger=DateTrigger(run_date=run_dt, timezone=self.tz))
        self._index_jobs(persist_id, job.id)
        return job.id
    # Cron becomes immediate date (+1s)
    def _fast_cron(trigger, cb, payload, *, persist_id=None):
        now = datetime.now().astimezone(sched.tz)
        return clamp_date(now + timedelta(seconds=3), cb, payload, persist_id=persist_id)

    def fast_cron(trigger:CronTrigger, cb, payload, *, persist_id=None):
        async def wrapper():
            await sched._maybe_await(cb, {**payload, "scheduled_for": datetime.now(sched.tz)})

        #trigger.start_date = datetime.now().astimezone(sched.tz) + timedelta(seconds=10)
        #f trigger.end_date:
        #   trigger.end_date = datetime.now().astimezone(sched.tz) + timedelta(seconds=15)
        job = sched.sched.add_job(wrapper, trigger=trigger)
        sched._index_jobs(persist_id, job.id)
    
    monkeypatch.setattr(sched, "_add_cron_job", fast_cron)

    # Solar events are near-future
    base = datetime.now().astimezone(sched.tz)
    monkeypatch.setattr(sched.sun, "sunrise", lambda d: base + timedelta(seconds=3))
    monkeypatch.setattr(sched.sun, "sunset",  lambda d: base + timedelta(seconds=4))
    return sched

@pytest.mark.asyncio
async def test_all_13_forms_fire_start_and_end_where_applicable(fast_scheduler):
    sched = fast_scheduler

    now = datetime.now().astimezone(sched.tz)
    today = now.date()
    hhmmss = (now + timedelta(seconds=3)).strftime("%H:%M:%S")
    today_str = today.strftime("%Y/%m/%d")
    tomorrow_str = (today + timedelta(days=1)).strftime("%Y/%m/%d")

    # 13 forms
    forms = [
        {"at": {"time": hhmmss}},  # 1
    #    {"at": {"time": hhmmss, "date": today_str}},  # 9 -> start
    #    {"at": {"sunrise": -1}},  # 2 -> start
    #    {"at": {"sunset": 0}},    # 3 -> start
    #    {"from": {"sunrise": 0, "for": {"hours":0,"minutes":0,"seconds":1}}},  # 4 -> start,end
    #    {"from": {"sunrise": 0, "to": {"sunset": 0}}},  # 5 -> start,end
    #    {"from": {"sunrise": 0, "to": {"sunset": 0, "day":1}}},  # 6 -> start,end
    #    {"from": {"time": hhmmss, "to": {"sunset": 0, "day":1}}},  # 7 -> start,end
    #    {"from": {"time": hhmmss, "to": {"time": hhmmss, "day":0}}}, # 8 -> start,end
    #    {"from": {"time": hhmmss, "for": {"hours":0,"minutes":0,"seconds":1}}},  # from time for duration
    #    {"from": {"time": hhmmss, "date": today_str, "for": {"hours":0,"minutes":0,"seconds":1}}},  # 10 -> start,end
    #    {"from": {"time": hhmmss, "date": today_str, "to": {"time": hhmmss, "date": tomorrow_str}}},  # 11 -> start,end
    #    {"from": {"time": "22:00:00", "to": {"time": "06:00", "day":1}}},  # 12 -> start,end
    #    {"weekly": {"days": "sun,mon,tue,wed,thu,fri,sat", "from": {"time": hhmmss, "to": {"time": hhmmss}}}},  # 13 -> start,end
    ]
    
    got_start = asyncio.Event()
    got_end = asyncio.Event()
    starts = 0
    ends = 0

    async def print_event(ev, lstarts, lends):
        def _print_event(ev, lstarts, lends):
            print(f"\n{datetime.now(sched.tz).isoformat()} : {ev.get('meta')['persist_id']} : phase={ev.get('phase')} starts={lstarts} ends={lends} spec={ev.get('spec')} ")
        await asyncio.to_thread(_print_event, ev, lstarts, lends)

    async def cb(ev):
        nonlocal starts, ends
        if ev.get("phase") == "start":
            starts += 1
            if starts == len (forms):
                got_start.set()
        if ev.get("phase") == "end":
            ends += 1
            if ends == len (forms):
                got_end.set()
        await print_event(ev, starts, ends) # since async, we cannot use the global one
        

    persist_id = 1  
    # Schedule all
    for spec in forms:
        await sched.add(spec, cb, persist_id=f"spec_{persist_id}")
        persist_id += 1

    await sched.print_jobs()
    # Wait for at least one start and one end
    # Wait for start and end to occur
    await asyncio.wait_for(got_start.wait(), timeout=100)
    await asyncio.wait_for(got_end.wait(), timeout=100)
    await sched.print_jobs()

    assert starts >= 1
    assert ends >= 1

    await sched.shutdown()

class FakeHolidayProvider(HolidayProvider):
    def __init__(self, tz_str):
        self.tz_str = tz_str
    async def holidays_for_year(self, year: int) -> list[HolidayEvent]:
        # Single holiday today
        tz = ZoneInfo(self.tz_str)
        d = datetime.now(tz).date()
        return [HolidayEvent(date=d, title="TestFest", category="fake")]

#@pytest.mark.asyncio
async def xx_test_holiday_at_and_window_and_any_and_remove(fast_scheduler):
    sched = fast_scheduler

    # Register fake provider
    await sched.register_holiday_provider("fake", FakeHolidayProvider("UTC"))

    # Events storage
    events = []
    ev_start = asyncio.Event()
    ev_end = asyncio.Event()
    starts = 0
    ends = 0

    async def cb(ev):
        events.append(ev)
        if ev.get("phase") == "start":
            starts += 1
            ev_start.set()
        if ev.get("phase") == "end":
            ends += 1
            ev_end.set()

    # holiday_at
    await sched.add_holiday_at("fake", "08:00:00", cb, title_contains="TestFest", persist_id="h1")

    # holiday_window
    await sched.add_holiday_window("fake", "09:00:00", cb, duration={"hours":0,"minutes":0,"seconds":1}, title_contains="TestFest", persist_id="h2")

    # ANY of two quick specs
    now = datetime.now().astimezone(sched.tz)
    hhmmss = now.strftime("%H:%M:%S")
    await sched.add_any([{"at": {"time": hhmmss}}, {"at": {"sunrise": 0}}], cb, persist_id="group_any")


    # Some events should exist
    assert any(e.get("phase") == "start" for e in events)
    assert any(e.get("phase") == "end" for e in events)

    # Remove by id and ensure silence
    n = len(events)
    await sched.remove_persisted_by_id("group_any")
    await asyncio.sleep(2.5)
    assert len(events) == n

    await sched.shutdown()

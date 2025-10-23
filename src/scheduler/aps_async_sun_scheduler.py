from __future__ import annotations
"""
Async APScheduler wrapper with sunrise/sunset support that understands the 13 schedule forms,
plus recurring-day helpers, composite schedules (add_any), persistence removal by persist_id,
and holiday plugins.
"""
from dataclasses import dataclass
from typing import Callable, Dict, Any, Optional, Tuple, List
import asyncio
import json
import os
from datetime import datetime, date, time, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from astral import LocationInfo
from astral.sun import sun

DEFAULT_GRACE_PERIOD = 1  # seconds = 10 minutes

# ----------------------- core types for holiday plugins -----------------------
@dataclass
class HolidayEvent:
    date: date
    title: str
    category: str
    start: Optional[datetime] = None
    end: Optional[datetime] = None
    raw: Optional[Dict[str, Any]] = None

class HolidayProvider:
    async def holidays_for_year(self, year: int) -> List[HolidayEvent]:  # async interface
        raise NotImplementedError

# ----------------------- helpers -----------------------

def _parse_hhmmss(s: str) -> time:
    try:
        p = [int(x) for x in s.split(":")]
        if len(p) == 2: return time(p[0], p[1], 00)
        if len(p) == 3: return time(p[0], p[1], p[2])
    except ValueError:
        print(f"Invalid time format, expected HH:MM or HH:MM:SS but got {s}")
        return time.now()

def _parse_date(s: str) -> date:
    y, m, d = s.split("/")
    return date(int(y), int(m), int(d))

def _to_td(d: Dict[str, int]) -> timedelta:
    return timedelta(hours=d.get("hours", 0), minutes=d.get("minutes", 0), seconds=d.get("seconds", 0))

DOW_NAME_TO_APS = {  # APScheduler day_of_week format (mon=0..sun=6)
    "mon": "0", "tue": "1", "wed": "2", "thu": "3", "fri": "4", "sat": "5", "sun": "6"
}

# ---------------------- sun provider ----------------------

@dataclass
class SunProvider:
    tz_str: str
    latitude: float
    longitude: float
    name: str = "Loc"
    region: str = "Region"

    def __post_init__(self):
        self.tz = ZoneInfo(self.tz_str)
        self.loc = LocationInfo(self.name, self.region, self.tz_str, self.latitude, self.longitude)
        self._cache: Dict[tuple, datetime] = {}

    def _get_cached(self, key: tuple, compute: Callable[[], datetime]) -> datetime:
        dt = self._cache.get(key)
        if dt is None:
            dt = compute()
            self._cache[key] = dt
            try:
                today = datetime.now(self.tz).date()
                for k in list(self._cache.keys()):
                    kd = k[0]
                    if isinstance(kd, date) and (kd - today).days < -5:
                        self._cache.pop(k, None)
            except Exception:
                pass
        return dt

    def sunrise(self, on_date: date) -> datetime:
        key = (on_date, "sunrise")
        def compute():
            s_ = sun(self.loc.observer, date=on_date, tzinfo=self.tz_str)
            return s_["sunrise"].astimezone(self.tz)
        return self._get_cached(key, compute)

    def sunset(self, on_date: date) -> datetime:
        key = (on_date, "sunset")
        def compute():
            s_ = sun(self.loc.observer, date=on_date, tzinfo=self.tz_str)
            return s_["sunset"].astimezone(self.tz)
        return self._get_cached(key, compute)

# -------------------- date rules (recurring days) ---------------------

def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    if not 1 <= month <= 12:
        raise ValueError("month must be 1..12")
    import calendar
    cal = calendar.Calendar()
    days = [d for d in cal.itermonthdates(year, month) if d.month == month and d.weekday() == weekday]
    if n == -1:
        return days[-1]
    if n < 1 or n > len(days):
        raise ValueError("n out of range for given month/weekdays")
    return days[n-1]

# -------------------- async scheduler ---------------------

class AsyncAPSunScheduler:
    """
    AsyncIO-based scheduler with APScheduler under the hood.
    Supports the 13 schedule forms, recurring dates, holidays (through plugins) plus sunrise/sunset via Astral.
    Also supports composite schedules (ANY using or logic) and removal by persist_id.

    Public API additions:
      • add_any(schedules, callback, meta=None, persist_id=None) -> list[str]
      • remove_persisted_by_id(persist_id) -> None
    """

    def __init__(self, tz_str: str, *, latitude: float, longitude: float, grace_period:int=DEFAULT_GRACE_PERIOD, persist_path: Optional[str] = None, loop: Optional[asyncio.AbstractEventLoop] = None):
        self.tz = ZoneInfo(tz_str)
        self.sun = SunProvider(tz_str, latitude, longitude)
        try:
            self.loop = loop or asyncio.get_running_loop()
        except Exception:
            # No running loop (e.g., created outside an async context) — create one
            self.loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self.loop)
        self.sched = AsyncIOScheduler(timezone=self.tz, event_loop=self.loop, job_defaults={"misfire_grace_time": grace_period, "coalesce": True, "replace_existing": True, "max_instances": 1})
        self.sched.start()
        self.persist_path = persist_path
        if self.persist_path:
            os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
            if not os.path.exists(self.persist_path):
                self._persist_write({"specs": []})
        # persist_id -> set(job_ids)
        self._persist_index: Dict[str, set[str]] = {}

    # ----------------------- persistence -----------------------
    def _persist_read(self) -> Dict[str, Any]:
        try:
            with open(self.persist_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"specs": []}

    def _persist_write(self, data: Dict[str, Any]):
        if not self.persist_path:
            return
        tmp = self.persist_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.persist_path)

    async def load_persisted(self, callback_resolver: Callable[[str], Callable[[Dict[str, Any]], Any]]):
        if not self.persist_path:
            return
        data = await asyncio.to_thread(self._persist_read)
        for rec in data.get("specs", []):
            cb = callback_resolver(rec["id"]) if callback_resolver else None
            if cb is None:
                continue
            await self.add(rec["spec"], cb, meta=rec.get("meta"), persist_id=rec["id"])  # idempotent add

    async def load_persisted_with_kinds(self, callback_resolver: Callable[[str], Callable[[Dict[str, Any]], Any]]):
        if not self.persist_path:
            return
        data = await asyncio.to_thread(self._persist_read)
        for rec in data.get("specs", []):
            cb = callback_resolver(rec["id"]) if callback_resolver else None
            if cb is None:
                continue
            spec = rec["spec"]
            kind = spec.get("_kind") if isinstance(spec, dict) else None
            if kind == "annual_fixed":
                await self.add_annual_fixed_day(spec["month"], spec["day"], spec["time"], cb, meta=rec.get("meta"), persist_id=rec["id"])
            elif kind == "annual_dates":
                await self.add_annual_dates(spec["dates"], spec["time"], cb, meta=rec.get("meta"), persist_id=rec["id"])
            elif kind == "annual_nth_weekday":
                await self.add_annual_nth_weekday(spec["month"], spec.get("weekday"), spec["nth"], spec["time"], cb, meta=rec.get("meta"), persist_id=rec["id"])
            elif kind == "holiday_at":
                await self.add_holiday_at(spec["provider"], spec["time"], cb,
                    title_contains=spec.get("title_contains"), categories=spec.get("categories"), years_ahead=spec.get("years_ahead", 1),
                    meta=rec.get("meta"), persist_id=rec["id"])
            elif kind == "any":
                await self.add_any(spec["schedules"], cb, meta=rec.get("meta"), persist_id=rec["id"])
            else:
                await self.add(spec, cb, meta=rec.get("meta"), persist_id=rec["id"])  # fallback

    async def remove_persisted(self, spec_id: str):
        if not self.persist_path:
            return
        data = await asyncio.to_thread(self._persist_read)
        data["specs"] = [r for r in data.get("specs", []) if r.get("id") != spec_id]
        await asyncio.to_thread(self._persist_write, data)

    async def print_jobs(self):
        def _print_jobs():
            print(f"now is {datetime.now(self.tz).isoformat()}")
            for job in self.sched.get_jobs():
                print(f"Job id={job.id}, next_run_time={job.next_run_time}, trigger={job.trigger}")
        await asyncio.to_thread(_print_jobs)

    async def _persist_upsert(self, spec_id: Optional[str], spec: Dict[str, Any], meta: Any):
        if not self.persist_path or not spec_id:
            return
        data = await asyncio.to_thread(self._persist_read)
        rest = [r for r in data.get("specs", []) if r.get("id") != spec_id]
        rest.append({"id": spec_id, "spec": spec, "meta": meta})
        await asyncio.to_thread(self._persist_write, {"specs": rest})

    def _index_jobs(self, persist_id: Optional[str], job_id: str):
        if not persist_id:
            return
        self._persist_index.setdefault(persist_id, set()).add(job_id)

    # -------------------------- public --------------------------
    async def add(self, spec: Dict[str, Any], callback: Callable[[Dict[str, Any]], Any], *, meta: Any = None, persist_id: Optional[str] = None) -> List[str]:
        meta_with_id = dict(meta or {})
        if persist_id:
            # store persist_id in meta passed to callbacks
            meta_with_id.setdefault("persist_id", persist_id)
        if "at" in spec:
            job_ids = await self._schedule_at(spec["at"], callback, spec, meta_with_id, persist_id)
        elif "from" in spec:
            job_ids = await self._schedule_range(spec["from"], callback, spec, meta_with_id, persist_id)
        elif "weekly" in spec:
            job_ids = await self._schedule_weekly(spec["weekly"], callback, spec, meta_with_id, persist_id)
        else:
            raise ValueError("Unsupported schedule spec")
        await self._persist_upsert(persist_id, spec, meta_with_id)
        return job_ids

    async def add_any(self, schedules: List[Dict[str, Any]], callback: Callable[[Dict[str, Any]], Any], *, meta: Any = None, persist_id: Optional[str] = None) -> List[str]:
        """
        Combine multiple schedule specs; fires callback if ANY becomes true.
        Child schedules receive child persist ids of the form f\"{persist_id}#<index>\".
        """
        async def on_trigger(ev: dict):
            if ev.get("phase") == "start":
                await self._maybe_await(callback, {"triggered_by": ev, "meta": meta})
        job_ids: List[str] = []
        for i, spec in enumerate(schedules):
            child_id = f"{persist_id}#{i}" if persist_id else None
            ids = await self.add(spec, on_trigger, meta=meta, persist_id=child_id)
            job_ids.extend(ids)
        # persist the composite spec under the base id
        if persist_id:
            await self._persist_upsert(persist_id, {"_kind": "any", "schedules": schedules}, meta)
        return job_ids

    async def remove_persisted_by_id(self, persist_id: str) -> None:
        """
        Remove all APScheduler jobs and persisted records associated with persist_id.
        Also removes children like \"persist_id#0\", \"persist_id#1\", etc.
        """
        # 1) remove jobs by index
        to_remove = set()
        for pid, ids in list(self._persist_index.items()):
            if pid == persist_id or (pid.startswith(persist_id) and pid[len(persist_id):].startswith("#")):
                to_remove |= set(ids)
                self._persist_index.pop(pid, None)
        for jid in to_remove:
            try:
                self.sched.remove_job(jid)
            except Exception:
                pass
        # 2) remove persisted specs from disk
        if self.persist_path:
            data = await asyncio.to_thread(self._persist_read)
            data["specs"] = [r for r in data.get("specs", []) if not (r.get("id") == persist_id or (isinstance(r.get("id"), str) and r["id"].startswith(persist_id + "#")))]
            await asyncio.to_thread(self._persist_write, data)

    async def cancel(self, job_ids: List[str]):
        for jid in job_ids:
            try:
                self.sched.remove_job(jid)
                # remove from any index sets
                for ids in self._persist_index.values():
                    ids.discard(jid)
            except Exception:
                pass

    async def shutdown(self):
        def _stop():
            try:
                self.sched.shutdown(wait=False)
            except Exception:
                pass
        await asyncio.to_thread(_stop)

    # ---------------------- internal utils ----------------------
    async def _maybe_await(self, cb, event: Dict[str, Any]):
        if asyncio.iscoroutinefunction(cb):
            await cb(event)
        else:
            res = cb(event)
            if asyncio.iscoroutine(res):
                await res

    def _add_cron_job(self, trigger: CronTrigger, cb, payload: Dict[str, Any], *, persist_id: Optional[str] = None) -> str:
        async def wrapper():
            await self._maybe_await(cb, {**payload, "scheduled_for": datetime.now(self.tz)})
        job = self.sched.add_job(wrapper, trigger=trigger)
        self._index_jobs(persist_id, job.id)
        return job.id

    def _add_date_job(self, run_dt: datetime, cb, payload: Dict[str, Any], *, persist_id: Optional[str] = None) -> str:
        async def wrapper():
            await self._maybe_await(cb, {**payload, "scheduled_for": run_dt})
        job = self.sched.add_job(wrapper, trigger=DateTrigger(run_date=run_dt, timezone=self.tz))
        self._index_jobs(persist_id, job.id)
        return job.id

    # ----------------------------- AT -----------------------------
    async def _schedule_at(self, at: Dict[str, Any], cb, spec, meta, persist_id) -> List[str]:
        # date+time one-shot
        if "date" in at and "time" in at:
            run_dt = datetime.combine(_parse_date(at["date"]), _parse_hhmmss(at["time"]), tzinfo=self.tz)
            return [self._add_date_job(run_dt, cb, {"phase": "start", "spec": {"at": at}, "meta": meta}, persist_id=persist_id)]

        # daily at time
        if "time" in at:
            t = _parse_hhmmss(at["time"])
            trig = CronTrigger(hour=t.hour, minute=t.minute, second=t.second, timezone=self.tz)
            return [self._add_cron_job(trig, cb, {"phase": "start", "spec": {"at": at}, "meta": meta}, persist_id=persist_id)]

        # sunrise/sunset with offset seconds
        if "sunrise" in at or "sunset" in at:
            async def schedule_next():
                now = datetime.now(self.tz)
                today = now.date()
                offset_secs = int(at.get("sunrise", at.get("sunset", 0)))
                offset = timedelta(seconds=offset_secs)
                dt = (self.sun.sunrise(today) if "sunrise" in at else self.sun.sunset(today)) + offset
                if dt <= now:
                    nxt = today + timedelta(days=1)
                    dt = (self.sun.sunrise(nxt) if "sunrise" in at else self.sun.sunset(nxt)) + offset
                async def fire_then_resched():
                    await self._maybe_await(cb, {"phase": "start", "scheduled_for": dt, "spec": {"at": at}, "meta": meta})
                    await schedule_next()
                self._add_date_job(dt, lambda ev: fire_then_resched(), {"phase": "start", "spec": {"at": at}, "meta": meta}, persist_id=persist_id)
                return dt
            nxt_dt = await schedule_next()
            return [f"sun-at:{nxt_dt.isoformat()}"]

        raise ValueError("Invalid 'at' spec")

    # ---------------------------- RANGE ----------------------------
    async def _schedule_range(self, frm: Dict[str, Any], cb, full_spec, meta, persist_id) -> List[str]:
        jobs: List[str] = []

        # explicit date/time ranges (forms 10, 11)
        if "date" in frm and "time" in frm:
            start_dt = datetime.combine(_parse_date(frm["date"]), _parse_hhmmss(frm["time"]), tzinfo=self.tz)
            if "for" in frm:
                end_dt = start_dt + _to_td(frm["for"])
            else:
                to = frm["to"]
                end_dt = datetime.combine(_parse_date(to["date"]), _parse_hhmmss(to["time"]), tzinfo=self.tz)
            jobs.append(self._add_date_job(start_dt, cb, {"phase": "start", "spec": {"from": frm}, "meta": meta}, persist_id=persist_id))
            jobs.append(self._add_date_job(end_dt, cb, {"phase": "end", "spec": {"from": frm}, "meta": meta}, persist_id=persist_id))
            return jobs

        # recurring ranges (no explicit date)
        def compute_window(base: date) -> Tuple[datetime, datetime]:
            if "time" in frm:
                start_dt = datetime.combine(base, _parse_hhmmss(frm["time"]), tzinfo=self.tz)
            elif "sunrise" in frm:
                start_dt = self.sun.sunrise(base) + timedelta(seconds=int(frm["sunrise"]))
            elif "sunset" in frm:
                start_dt = self.sun.sunset(base) + timedelta(seconds=int(frm["sunset"]))
            else:
                raise ValueError("Invalid 'from' anchor")
            if "for" in frm:
                end_dt = start_dt + _to_td(frm["for"])
            else:
                to = frm["to"]
                day_shift = int(to.get("day", 0))
                to_date = base + timedelta(days=day_shift)
                if "time" in to:
                    end_dt = datetime.combine(to_date, _parse_hhmmss(to["time"]), tzinfo=self.tz)
                elif "sunrise" in to:
                    end_dt = self.sun.sunrise(to_date) + timedelta(seconds=int(to["sunrise"]))
                elif "sunset" in to:
                    end_dt = self.sun.sunset(to_date) + timedelta(seconds=int(to["sunset"]))
                else:
                    raise ValueError("Invalid 'to' anchor")
                if end_dt <= start_dt and "date" not in to:
                    end_dt += timedelta(days=1)
            return start_dt, end_dt

        # pure time daily starts -> cron for start; end scheduled as one-shot each day
        if "time" in frm and ("for" in frm or ("to" in frm and "time" in frm["to"])):
            t = _parse_hhmmss(frm["time"])
            async def on_start():
                today = datetime.now(self.tz).date()
                start_dt, end_dt = compute_window(today)
                await self._maybe_await(cb, {"phase": "start", "scheduled_for": start_dt, "spec": full_spec, "meta": meta})
                self._add_date_job(end_dt, cb, {"phase": "end", "spec": full_spec, "meta": meta}, persist_id=persist_id)
            trig = CronTrigger(hour=t.hour, minute=t.minute, second=t.second, timezone=self.tz)
            jobs.append(self._add_cron_job(trig, lambda ev=None: on_start(), {"phase": "start", "spec": full_spec, "meta": meta}, persist_id=persist_id))
            return jobs

        # sunrise/sunset involvement -> schedule per day then reschedule
        async def schedule_today_or_tomorrow():
            now = datetime.now(self.tz)
            start_dt, end_dt = compute_window(now.date())
            if start_dt <= now:
                start_dt, end_dt = compute_window(now.date() + timedelta(days=1))

            async def fire_and_reschedule():
                await self._maybe_await(cb, {"phase": "start", "scheduled_for": start_dt, "spec": full_spec, "meta": meta})
                self._add_date_job(end_dt, cb, {"phase": "end", "spec": full_spec, "meta": meta}, persist_id=persist_id)
                await schedule_today_or_tomorrow()

            self._add_date_job(start_dt, lambda ev=None: fire_and_reschedule(), {"phase": "start", "spec": full_spec, "meta": meta}, persist_id=persist_id)
            return start_dt

        nxt = await schedule_today_or_tomorrow()
        jobs.append(f"sun-range:{nxt.isoformat()}")
        return jobs

    # ---------------------------- WEEKLY ---------------------------
    async def _schedule_weekly(self, w: Dict[str, Any], cb, spec, meta, persist_id) -> List[str]:
        days_str = w.get("days", "")
        day_names = [d for d in days_str.split(",") if d]
        for d in day_names:
            if d not in DOW_NAME_TO_APS:
                raise ValueError(f"Invalid weekday: {d}")
        frm = w.get("from", {})
        if "time" not in frm:
            raise ValueError("weekly.from must include time")
        start_t = _parse_hhmmss(frm["time"])
        jobs: List[str] = []
        day_of_week = ",".join(DOW_NAME_TO_APS[d] for d in day_names)
        trig = CronTrigger(day_of_week=day_of_week, hour=start_t.hour, minute=start_t.minute, second=start_t.second, timezone=self.tz)

        if "to" in frm and "time" in frm["to"]:
            end_t = _parse_hhmmss(frm["to"]["time"])
            async def on_start():
                now = datetime.now(self.tz)
                start_dt = datetime.combine(now.date(), start_t, tzinfo=self.tz)
                end_dt = datetime.combine(now.date(), end_t, tzinfo=self.tz)
                if end_dt <= start_dt:
                    end_dt += timedelta(days=1)
                await self._maybe_await(cb, {"phase": "start", "scheduled_for": start_dt, "spec": spec, "meta": meta})
                self._add_date_job(end_dt, cb, {"phase": "end", "spec": spec, "meta": meta}, persist_id=persist_id)
            jobs.append(self._add_cron_job(trig, lambda ev=None: on_start(), {"phase": "start", "spec": spec, "meta": meta}, persist_id=persist_id))
            return jobs

        if "for" in frm:
            dur = _to_td(frm["for"])
            async def on_start():
                now = datetime.now(self.tz)
                start_dt = datetime.combine(now.date(), start_t, tzinfo=self.tz)
                end_dt = start_dt + dur
                await self._maybe_await(cb, {"phase": "start", "scheduled_for": start_dt, "spec": spec, "meta": meta})
                self._add_date_job(end_dt, cb, {"phase": "end", "spec": spec, "meta": meta}, persist_id=persist_id)
            jobs.append(self._add_cron_job(trig, lambda ev=None: on_start(), {"phase": "start", "spec": spec, "meta": meta}, persist_id=persist_id))
            return jobs

        raise ValueError("weekly.from must include 'to.time' or 'for'")

    # --------------------- HOLIDAY SCHEDULING API ---------------------
    async def register_holiday_provider(self, name: str, provider: HolidayProvider):
        if not hasattr(self, "_holiday_providers"):
            self._holiday_providers: Dict[str, HolidayProvider] = {}
        self._holiday_providers[name] = provider

    async def list_holidays(self, provider_name: str, year: Optional[int] = None) -> List[HolidayEvent]:
        prov = getattr(self, "_holiday_providers", {}).get(provider_name)
        if prov is None:
            raise ValueError(f"Unknown holiday provider '{provider_name}'")
        yr = year or datetime.now(self.tz).year
        return await prov.holidays_for_year(yr)

    async def add_holiday_at(self, provider_name: str, time_hhmm: str, callback: Callable[[Dict[str, Any]], Any], *, title_contains: Optional[str] = None, categories: Optional[List[str]] = None, years_ahead: int = 1, meta: Any = None, persist_id: Optional[str] = None) -> List[str]:
        prov = getattr(self, "_holiday_providers", {}).get(provider_name)
        if prov is None:
            raise ValueError(f"Unknown holiday provider '{provider_name}'")
        t = _parse_hhmmss(time_hhmm)
        now = datetime.now(self.tz)
        start_year = now.year
        job_ids: List[str] = []

        def match(ev: HolidayEvent) -> bool:
            ok = True
            if title_contains:
                ok = ok and (title_contains.lower() in (ev.title or "").lower())
            if categories:
                ok = ok and (ev.category in set(categories))
            return ok

        async def schedule_for_year(yr: int):
            evs = await prov.holidays_for_year(yr)
            for ev in evs:
                if not match(ev):
                    continue
                run_dt = datetime.combine(ev.date, t, tzinfo=self.tz)
                async def fire_then_next(dd=run_dt, y=yr):
                    await self._maybe_await(callback, {"phase":"start", "scheduled_for": dd, "spec": {"_kind":"holiday_at","provider":provider_name,"time":time_hhmm,"title_contains":title_contains,"categories":categories}, "meta": meta})
                    await schedule_for_year(y + years_ahead)
                job_ids.append(self._add_date_job(run_dt, lambda ev=None, _fn=fire_then_next: _fn(), {"phase":"start", "spec": {"_kind":"holiday_at","provider":provider_name,"time":time_hhmm,"title_contains":title_contains,"categories":categories}, "meta": meta}, persist_id=persist_id))

        for yr in range(start_year, start_year + years_ahead + 1):
            await schedule_for_year(yr)

        persist_spec = {"_kind":"holiday_at","provider":provider_name,"time":time_hhmm,"title_contains":title_contains,"categories":categories,"years_ahead":years_ahead}
        await self._persist_upsert(persist_id, persist_spec, meta)
        return job_ids

    async def add_holiday_window(self, provider_name: str, from_time_hhmm: str, callback: Callable[[Dict[str, Any]], Any], *, to_time_hhmm: Optional[str] = None, duration: Optional[Dict[str,int]] = None, title_contains: Optional[str] = None, categories: Optional[List[str]] = None, years_ahead: int = 1, meta: Any = None, persist_id: Optional[str] = None) -> List[str]:
        if (to_time_hhmm is None) == (duration is None):
            raise ValueError("Provide exactly one of to_time_hhmm or duration")
        prov = getattr(self, "_holiday_providers", {}).get(provider_name)
        if prov is None:
            raise ValueError(f"Unknown holiday provider '{provider_name}'")
        t_from = _parse_hhmmss(from_time_hhmm)
        now = datetime.now(self.tz)
        start_year = now.year
        job_ids: List[str] = []

        def match(ev: HolidayEvent) -> bool:
            ok = True
            if title_contains:
                ok = ok and (title_contains.lower() in (ev.title or "").lower())
            if categories:
                ok = ok and (ev.category in set(categories))
            return ok

        async def schedule_for_year(yr: int):
            evs = await prov.holidays_for_year(yr)
            for ev in evs:
                if not match(ev):
                    continue
                start_dt = datetime.combine(ev.date, t_from, tzinfo=self.tz)
                if to_time_hhmm:
                    t_to = _parse_hhmmss(to_time_hhmm)
                    end_dt = datetime.combine(ev.date, t_to, tzinfo=self.tz)
                    if end_dt <= start_dt:
                        end_dt += timedelta(days=1)
                else:
                    end_dt = start_dt + _to_td(duration or {})

                async def fire_then_next(sdt=start_dt, edt=end_dt, y=yr):
                    await self._maybe_await(callback, {"phase":"start","scheduled_for": sdt, "spec": {"_kind":"holiday_window","provider":provider_name,"from":from_time_hhmm,"to":to_time_hhmm,"duration":duration,"title_contains":title_contains,"categories":categories}, "meta": meta})
                    self._add_date_job(edt, callback, {"phase":"end","spec": {"_kind":"holiday_window","provider":provider_name,"from":from_time_hhmm,"to":to_time_hhmm,"duration":duration,"title_contains":title_contains,"categories":categories}, "meta": meta}, persist_id=persist_id)
                    await schedule_for_year(y + years_ahead)

                job_ids.append(self._add_date_job(start_dt, lambda ev=None, _fn=fire_then_next: _fn(), {"phase":"start", "spec": {"_kind":"holiday_window","provider":provider_name,"from":from_time_hhmm,"to":to_time_hhmm,"duration":duration,"title_contains":title_contains,"categories":categories}, "meta": meta}, persist_id=persist_id))

        for yr in range(start_year, start_year + years_ahead + 1):
            await schedule_for_year(yr)

        persist_spec = {"_kind":"holiday_window","provider":provider_name,"from":from_time_hhmm,"to":to_time_hhmm,"duration":duration,"title_contains":title_contains,"categories":categories,"years_ahead":years_ahead}
        await self._persist_upsert(persist_id, persist_spec, meta)
        return job_ids

    # --------------------- RECURRING DAYS API ---------------------
    async def add_annual_fixed_day(self, month: int, day: int, time_hhmm: str, callback: Callable[[Dict[str, Any]], Any], *, meta: Any = None, persist_id: Optional[str] = None) -> List[str]:
        t = _parse_hhmmss(time_hhmm)
        trig = CronTrigger(month=month, day=day, hour=t.hour, minute=t.minute, second=t.second, timezone=self.tz)
        job_id = self._add_cron_job(trig, callback, {"phase": "start", "spec": {"_kind":"annual_fixed","month":month,"day":day,"time":time_hhmm}, "meta": meta}, persist_id=persist_id)
        await self._persist_upsert(persist_id, {"_kind":"annual_fixed","month":month,"day":day,"time":time_hhmm}, meta)
        return [job_id]

    async def add_annual_dates(self, dates: List[tuple[int,int]], time_hhmm: str, callback: Callable[[Dict[str, Any]], Any], *, meta: Any = None, persist_id: Optional[str] = None) -> List[str]:
        t = _parse_hhmmss(time_hhmm)
        job_ids: List[str] = []
        for (m, d) in dates:
            trig = CronTrigger(month=m, day=d, hour=t.hour, minute=t.minute, second=t.second, timezone=self.tz)
            job_ids.append(self._add_cron_job(trig, callback, {"phase":"start", "spec": {"_kind":"annual_fixed","month":m,"day":d,"time":time_hhmm}, "meta": meta}, persist_id=persist_id))
        await self._persist_upsert(persist_id, {"_kind":"annual_dates","dates":dates,"time":time_hhmm}, meta)
        return job_ids

    async def add_annual_nth_weekday(self, month: int, weekday: int | str, nth: int, time_hhmm: str, callback: Callable[[Dict[str, Any]], Any], *, meta: Any = None, persist_id: Optional[str] = None) -> List[str]:
        w = self._weekday_to_int(weekday)
        t = _parse_hhmmss(time_hhmm)
        tz = self.tz

        async def schedule_year(year: int):
            d = _nth_weekday_of_month(year, month, w, nth)
            run_dt = datetime.combine(d, t, tzinfo=tz)
            async def fire_and_next():
                await self._maybe_await(callback, {"phase": "start", "scheduled_for": run_dt, "spec": {"_kind":"annual_nth_weekday","month":month,"weekday":w,"nth":nth,"time":time_hhmm}, "meta": meta})
                await schedule_year(year + 1)
            self._add_date_job(run_dt, lambda ev=None: fire_and_next(), {"phase":"start", "spec": {"_kind":"annual_nth_weekday","month":month,"weekday":w,"nth":nth,"time":time_hhmm}, "meta": meta}, persist_id=persist_id)
            return run_dt

        now = datetime.now(tz)
        first_dt = await schedule_year(now.year)
        if first_dt and first_dt <= now:
            await schedule_year(now.year + 1)
        await self._persist_upsert(persist_id, {"_kind":"annual_nth_weekday","month":month,"weekday":w,"nth":nth,"time":time_hhmm}, meta)
        return [f"annual-nth-weekday:{month}:{w}:{nth}:{time_hhmm}"]

    # ---------------------- utilities ----------------------
    def _weekday_to_int(self, weekday: int | str) -> int:
        if isinstance(weekday, int):
            if 0 <= weekday <= 6:
                return weekday
            raise ValueError("weekday int must be 0..6 (Mon..Sun)")
        name = str(weekday).strip().lower()
        mapping = {"mon":0, "tue":1, "wed":2, "thu":3, "fri":4, "sat":5, "sun":6}
        if name not in mapping:
            raise ValueError("weekday must be mon..sun")
        return mapping[name]

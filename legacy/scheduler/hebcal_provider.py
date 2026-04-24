from __future__ import annotations
"""
Hebcal holiday provider for the AsyncAPSunScheduler project.
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from zoneinfo import ZoneInfo

from .aps_async_sun_scheduler import HolidayProvider, HolidayEvent

try:
    import aiohttp
except Exception:  # optional dependency
    aiohttp = None

class HebcalHolidayProvider(HolidayProvider):
    """Fetches Jewish holidays from Hebcal.
    https://www.hebcal.com/home/developer-apis
    """
    BASE = "https://www.hebcal.com/hebcal"

    def __init__(self, tz_str: str, latitude: float, longitude: float, include: str | None = None):
        self.tz_str = tz_str
        self.tz = ZoneInfo(tz_str)
        self.latitude = latitude
        self.longitude = longitude
        self.include = include or "maj,min,mod,nx,mf,ss,s,c"
        self._cache: Dict[int, List[HolidayEvent]] = {}

    async def holidays_for_year(self, year: int) -> List[HolidayEvent]:
        if year in self._cache:
            return self._cache[year]
        if aiohttp is None:
            raise RuntimeError("aiohttp not installed — required for HebcalHolidayProvider")
        params = {
            "v": "1",
            "cfg": "json",
            "year": str(year),
            "maj": "on", "min": "on", "mod": "on", "nx": "on", "mf": "on", "ss": "on", "s": "on", "c": "on",
            "geo": "pos",
            "latitude": str(self.latitude),
            "longitude": str(self.longitude),
            "tzid": self.tz_str,
        }
        if self.include != "maj,min,mod,nx,mf,ss,s,c":
            for k in ["maj","min","mod","nx","mf","ss","s","c"]:
                params.pop(k, None)
            for k in self.include.split(","):
                params[k.strip()] = "on"
        async with aiohttp.ClientSession() as sess:
            async with sess.get(self.BASE, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()
        events: List[HolidayEvent] = []
        for item in data.get("items", []):
            cat = item.get("category") or item.get("subcat") or "holiday"
            dt_str = item.get("date")
            start_dt: Optional[datetime] = None
            if dt_str:
                try:
                    start_dt = datetime.fromisoformat(dt_str)
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=self.tz)
                    else:
                        start_dt = start_dt.astimezone(self.tz)
                except Exception:
                    start_dt = None
            events.append(HolidayEvent(
                date=(start_dt.date() if start_dt else datetime(year, int(item.get("month", 1)), int(item.get("day", 1)), tzinfo=self.tz).date()),
                title=item.get("title", ""),
                category=str(cat),
                start=start_dt,
                end=None,
                raw=item,
            ))
        self._cache[year] = events
        return events

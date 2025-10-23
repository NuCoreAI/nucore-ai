from __future__ import annotations
"""
U.S. Federal holiday provider for the AsyncAPSunScheduler project.
"""
from typing import List
from datetime import date, timedelta
from zoneinfo import ZoneInfo

from .aps_async_sun_scheduler import HolidayProvider, HolidayEvent

class USFederalHolidayProvider(HolidayProvider):
    """Generates U.S. Federal holidays (observed) locally without HTTP.
    Categories: "federal". Observance: Saturday -> Friday, Sunday -> Monday.
    """
    def __init__(self, tz_str: str):
        self.tz = ZoneInfo(tz_str)
        self._cache: dict[int, List[HolidayEvent]] = {}

    async def holidays_for_year(self, year: int) -> List[HolidayEvent]:
        if year in self._cache:
            return self._cache[year]

        def observed(d: date) -> date:
            wd = d.weekday()  # Mon=0..Sun=6
            if wd == 5:  # Saturday
                return d - timedelta(days=1)
            if wd == 6:  # Sunday
                return d + timedelta(days=1)
            return d

        def nth_wday(month: int, weekday: int, n: int) -> date:
            from calendar import Calendar
            cal = Calendar()
            days = [d for d in cal.itermonthdates(year, month) if d.month == month and d.weekday() == weekday]
            return days[-1] if n == -1 else days[n-1]

        evs: List[HolidayEvent] = []

        def add(d: date, title: str):
            evs.append(HolidayEvent(date=d, title=title, category="federal", start=None, end=None, raw={}))

        add(observed(date(year,1,1)), "New Year's Day (Observed)")
        add(nth_wday(1, 0, 3), "Martin Luther King Jr. Day")
        add(nth_wday(2, 0, 3), "Washington's Birthday")
        add(nth_wday(5, 0, -1), "Memorial Day")
        add(observed(date(year,6,19)), "Juneteenth National Independence Day (Observed)")
        add(observed(date(year,7,4)), "Independence Day (Observed)")
        add(nth_wday(9, 0, 1), "Labor Day")
        add(nth_wday(10, 0, 2), "Columbus Day / Indigenous Peoples' Day")
        add(observed(date(year,11,11)), "Veterans Day (Observed)")
        add(nth_wday(11, 3, 4), "Thanksgiving Day")
        add(observed(date(year,12,25)), "Christmas Day (Observed)")

        self._cache[year] = evs
        return evs

#This class represents schedules in IoX. Schedules can be categorized
# in the following 13 categories
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod

"""
1. Specific time:
<schedule>
<at><time>number of seconds since midnight</time></at>
</schedule>

2. Sunrise with offset:
<schedule>
<at><sunrise>offset in seconds (- for before, + for after)</sunrise></at>
</schedule>

3. Sunset with offset:
<schedule>
<at><sunset>offset in seconds (- for before, + for after)</sunset></at>
</schedule>

4. Time range from sunrise:
<schedule>
<from><sunrise>offset in seconds (- for before, + for after)</sunrise></from>
<for><hours>hours in integer</hours><minutes>minutes in integer</minutes><seconds>seconds in integer</seconds></for>
</schedule>

5. Time range from sunrise to sunset:
<schedule>
<from><sunrise>offset in seconds (- for before, + for after)</sunrise></from>
<to><sunset>offset in seconds (- for before, + for after)</sunset></to>
</schedule>

6. Time range from sunrise to sunset on a different day:
<schedule>
<from><sunrise>offset in seconds (- for before, + for after)</sunrise></from>
<to><sunset>offset in seconds (- for before, + for after)</sunset><day>number of days after the from. 1 = next day, 2 = 2 days later, ...</day></to>
</schedule>

7. Time range from specific time to sunset:
<schedule>
<from><time>number of seconds since midnight</time></from>
<to><sunset>offset in seconds (- for before, + for after)</sunset><day>number of days after the from. 1 = next day, 2 = 2 days later, ...</day></to>
</schedule>

8. Time range between specific times:
<schedule>
<from><time>number of seconds since midnight</time></from>
<to><time>number of seconds since midnight</time><day>number of days after the from. 1 = next day, 2 = 2 days later, ...</day></to>
</schedule>

9. Specific time on a specific date:
<schedule>
<at><time>number of seconds since midnight</time><date>YYYY/MM/DD</date></at>
</schedule>

10. Time range from specific time and date:
<schedule>
<from><time>number of seconds since midnight</time><date>YYYY/MM/DD</date></from>
<for><hours>hours</hours><minutes>minutes</minutes><seconds>seconds</seconds></for>
</schedule>

11. Time range between specific times and dates:
<schedule>
<from><time>number of seconds since midnight</time><date>YYYY/MM/DD</date></from>
<to><time>number of seconds since midnight</time><day>number of days after from. 1 = next day, 2 = 2 days later, ...</day></to>
</schedule>

12. Weekly schedule for specific days and times:
<schedule>
<daysofweek>
choose specific days of the week from the following
<sat /><sun /><mon /><tue /><wed /><thu /><fri />
</daysofweek>
<from><time>number of seconds since midnight</time></from>
<to><time>number of seconds since midnight</time></to>
</schedule>

13. Weekly schedule for specific days with duration:
<schedule>
<daysofweek>
choose specific days of the week from the following
<sat /><sun /><mon /><tue /><wed /><thu /><fri />
</daysofweek>
<from><time>number of seconds since midnight</time></from>
<for><hours>hours</hours><minutes>minutes</minutes><seconds>seconds</seconds></for>
</schedule>
"""


class NuCoreSchedule(ABC):
    def __init__(self):
        super().__init__()

    @abstractmethod
    def parse_schedule(self, schedule_xml):
        """Parses the schedule from the given XML string."""
        pass

    @abstractmethod
    def get_schedule(self):
        """Returns the schedule in XML"""
        pass

class AtSchedule(NuCoreSchedule):
    """
    Schedule that triggers at a specific time (number of seconds since midnight)
    <at><time>number of seconds since midnight</time></at>
    """
    def __init__(self, at: int=None):
        super().__init__()
        self.at:int = at 

    def parse_schedule(self, schedule_xml):
        root = ET.fromstring(schedule_xml)
        at_elem = root.find('at')
        if at_elem is not None:
            time_elem = at_elem.find('time')
            if time_elem is not None:
                self.at = int(time_elem.text)

    def get_schedule(self) -> str:
        schedule_xml = f"""
        <schedule>
            <at>
                <time>{self.at}</time>
            </at>
        </schedule>
        """
        return schedule_xml

class SunriseSchedule(NuCoreSchedule):
    """
    Schedule that triggers at sunrise.
    <sunrise>offset in seconds (- for before, + for after)</sunrise>
    """
    def __init__(self, sunrise_offset: int=None):
        super().__init__()
        self.sunrise_offset = sunrise_offset

    def parse_schedule(self, schedule_xml):
        root = ET.fromstring(schedule_xml)
        sunrise_elem = root.find('sunrise')
        try:
            if sunrise_elem is not None:
                self.sunrise_offset = int(sunrise_elem.text)
        except ValueError:
            self.sunrise_offset = None

    def get_schedule(self) -> str:
        schedule_xml = f"""
        <schedule>
            <sunrise>
                {self.sunrise_offset}
            </sunrise>
        </schedule>
        """
        return schedule_xml
    
class SunsetSchedule(NuCoreSchedule):
    """
    Schedule that triggers at sunset.
    <sunset>offset in seconds (- for before, + for after)</sunset>
    """
    def __init__(self, sunset_offset: int=None):
        super().__init__()
        self.sunset_offset = sunset_offset

    def parse_schedule(self, schedule_xml):
        root = ET.fromstring(schedule_xml)
        sunset_elem = root.find('sunset')
        if sunset_elem is not None:
            self.sunset_offset = int(sunset_elem.text)

    def get_schedule(self) -> str:
        schedule_xml = f"""
        <schedule>
            <sunset>
                {self.sunset_offset}
            </sunset>
        </schedule>
        """
        return schedule_xml
    
class SunriseRangeSchedule(NuCoreSchedule):
    """
    Schedule that triggers at sunrise between a specific range.
    <schedule>
        <from><sunrise>offset in seconds (- for before, + for after)</sunrise></from>
        <for><hours>hours in integer</hours><minutes>minutes in integer</minutes><seconds>seconds in integer</seconds></for>
    </schedule>
    """
    def __init__(self, start_offset: int=None, duration: dict=None):
        super().__init__()
        self.start_offset = start_offset
        self.duration = duration

    def parse_schedule(self, schedule_xml):
        root = ET.fromstring(schedule_xml)
        from_elem = root.find('from/sunrise')
        for_elem = root.find('for')
        try:
            if from_elem is not None:
                self.start_offset = int(from_elem.text)
            if for_elem is not None:
                self.duration = {
                    'hours': int(for_elem.find('hours').text),
                    'minutes': int(for_elem.find('minutes').text),
                    'seconds': int(for_elem.find('seconds').text)
                }
        except ValueError:
            self.start_offset = None
            self.duration = None

    def get_schedule(self) -> str:
        schedule_xml = f"""
        <schedule>
            <from><sunrise>{self.start_offset}</sunrise></from>
            <for>
                <hours>{self.duration.get('hours', 0)}</hours>
                <minutes>{self.duration.get('minutes', 0)}</minutes>
                <seconds>{self.duration.get('seconds', 0)}</seconds>
            </for>
        </schedule>
        """
        return schedule_xml

class SunriseToSunsetSchedule(NuCoreSchedule):
    """
        Schedule that triggers from sunrise to sunset 
        <schedule>
        <from><sunrise>offset in seconds (- for before, + for after)</sunrise></from>
        <to><sunset>offset in seconds (- for before, + for after)</sunset></to>
        </schedule>
    """
    def __init__(self, sunrise_offset: int=None, sunset_offset: int=None):
        super().__init__()
        self.sunrise_offset = sunrise_offset
        self.sunset_offset = sunset_offset

    def parse_schedule(self, schedule_xml):
        root = ET.fromstring(schedule_xml)
        from_elem = root.find('from/sunrise')
        to_elem = root.find('to/sunset')
        try:
            if from_elem is not None:
                self.sunrise_offset = int(from_elem.text)
            if to_elem is not None:
                self.sunset_offset = int(to_elem.text)
        except ValueError:
            self.sunrise_offset = None
            self.sunset_offset = None

    def get_schedule(self) -> str:
        schedule_xml = f"""
        <schedule>
            <from><sunrise>{self.sunrise_offset}</sunrise></from>
            <to><sunset>{self.sunset_offset}</sunset></to>
        </schedule>
        """
        return schedule_xml
    
class SunriseToSunsetDifferentDaySchedule(NuCoreSchedule):
    """
    Time range from sunrise to sunset on a different day:
    <schedule>
        <from><sunrise>offset in seconds (- for before, + for after)</sunrise></from>
        <to><sunset>offset in seconds (- for before, + for after)</sunset><day>number of days after the from. 1 = next day, 2 = 2 days later, ...</day></to>
    </schedule>
    """
    def __init__(self):
        super().__init__()
        self.sunrise_offset = None
        self.sunset_offset = None
        self.days_after = None

    def parse_schedule(self, schedule_xml):
        root = ET.fromstring(schedule_xml)
        from_elem = root.find('from/sunrise')
        to_elem = root.find('to/sunset')
        day_elem = root.find('to/day')
        try:
            if from_elem is not None:
                self.sunrise_offset = int(from_elem.text)
            if to_elem is not None:
                self.sunset_offset = int(to_elem.text)
            if day_elem is not None:
                self.days_after = int(day_elem.text)
        except ValueError:
            self.sunrise_offset = None
            self.sunset_offset = None
            self.days_after = None

    def get_schedule(self) -> str:
        schedule_xml = f"""
        <schedule>
            <from><sunrise>{self.sunrise_offset}</sunrise></from>
            <to><sunset>{self.sunset_offset}</sunset><day>{self.days_after}</day></to>
        </schedule>
        """
        return schedule_xml
    
class TimeToSunsetSchedule(NuCoreSchedule):
    """
    Time range from specific time to sunset:
    <schedule>
    <from><time>number of seconds since midnight</time></from>
    <to><sunset>offset in seconds (- for before, + for after)</sunset><day>number of days after the from. 1 = next day, 2 = 2 days later, ...</day></to>
    </schedule>
    """
    def __init__(self):
        super().__init__()
        self.time_offset = None
        self.sunset_offset = None
        self.days_after = None

    def parse_schedule(self, schedule_xml):
        root = ET.fromstring(schedule_xml)
        from_elem = root.find('from/time')
        to_elem = root.find('to/sunset')
        day_elem = root.find('to/day')
        try:
            if from_elem is not None:
                self.time_offset = int(from_elem.text)
            if to_elem is not None:
                self.sunset_offset = int(to_elem.text)
            if day_elem is not None:
                self.days_after = int(day_elem.text)
        except ValueError:
            self.time_offset = None
            self.sunset_offset = None
            self.days_after = None

    def get_schedule(self) -> str:
        schedule_xml = f"""
        <schedule>
            <from><time>{self.time_offset}</time></from>
            <to><sunset>{self.sunset_offset}</sunset><day>{self.days_after}</day></to>
        </schedule>
        """
        return schedule_xml
    

class TimeRangeSchedule(NuCoreSchedule):
    """
        Time range between specific times:
        <schedule>
        <from><time>number of seconds since midnight</time></from>
        <to><time>number of seconds since midnight</time><day>number of days after the from. 1 = next day, 2 = 2 days later, ...</day></to>
        </schedule>
    """
    def __init__(self):
        super().__init__()
        self.start_time_offset = None
        self.end_time_offset = None
        self.days_after = None

    def parse_schedule(self, schedule_xml):
        root = ET.fromstring(schedule_xml)
        from_elem = root.find('from/time')
        to_elem = root.find('to/time')
        day_elem = root.find('to/day')
        try:
            if from_elem is not None:
                self.start_time_offset = int(from_elem.text)
            if to_elem is not None:
                self.end_time_offset = int(to_elem.text)
            if day_elem is not None:
                self.days_after = int(day_elem.text)
        except ValueError:
            self.start_time_offset = None
            self.end_time_offset = None
            self.days_after = None

    def get_schedule(self) -> str:
       schedule_xml = f"""
       <schedule>
           <from><time>{self.start_time_offset}</time></from>
           <to><time>{self.end_time_offset}</time><day>{self.days_after}</day></to>
       </schedule>
       """
       return schedule_xml.strip()

class SpecificTimeAndDateSchedule(NuCoreSchedule):
    """
        Specific time on a specific date:
        <schedule>
        <at><time>number of seconds since midnight</time><date>YYYY/MM/DD</date></at>
        </schedule>
    """
    def __init__(self):
        super().__init__()
        self.time_offset = None
        self.date = None

    def parse_schedule(self, schedule_xml):
        root = ET.fromstring(schedule_xml)
        at_elem = root.find('at')
        time_elem = at_elem.find('time') if at_elem is not None else None
        date_elem = at_elem.find('date') if at_elem is not None else None
        try:
            if time_elem is not None:
                self.time_offset = int(time_elem.text)
            if date_elem is not None:
                self.date = date_elem.text
        except ValueError:
            self.time_offset = None
            self.date = None

    def get_schedule(self) -> str:
        schedule_xml = f"""
        <schedule>
            <at><time>{self.time_offset}</time><date>{self.date}</date></at>
        </schedule>
        """
        return schedule_xml.strip()
    
class TimeRangeFromDateSchedule(NuCoreSchedule):
    """
        Time range from specific time and date:
        <schedule>
        <from><time>number of seconds since midnight</time><date>YYYY/MM/DD</date></from>
        <for><hours>hours</hours><minutes>minutes</minutes><seconds>seconds</seconds></for>
        </schedule>
    """

    def __init__(self):
        super().__init__()
        self.start_time_offset = None
        self.start_date = None
        self.end_time_offset = None
        self.end_date = None
        self.duration_hours = None
        self.duration_minutes = None
        self.duration_seconds = None

    def parse_schedule(self, schedule_xml):
        root = ET.fromstring(schedule_xml)
        from_elem = root.find('from')
        to_elem = root.find('to')
        try:
            if from_elem is not None:
                time_elem = from_elem.find('time')
                date_elem = from_elem.find('date')
                if time_elem is not None:
                    self.start_time_offset = int(time_elem.text)
                if date_elem is not None:
                    self.start_date = date_elem.text
            if to_elem is not None:
                time_elem = to_elem.find('time')
                date_elem = to_elem.find('date')
                if time_elem is not None:
                    self.end_time_offset = int(time_elem.text)
                if date_elem is not None:
                    self.end_date = date_elem.text
        except ValueError:
            self.start_time_offset = None
            self.start_date = None
            self.end_time_offset = None
            self.end_date = None

    def get_schedule(self) -> str:
        schedule_xml = f"""
        <schedule>
            <from><time>{self.start_time_offset}</time><date>{self.start_date}</date></from>
            <to><time>{self.end_time_offset}</time><date>{self.end_date}</date></to>
        </schedule>
        """
        return schedule_xml.strip()
    
class TimeRangeBetweenDatesSchedule(NuCoreSchedule):
    """
        Time range between specific times and dates:
        <schedule>
        <from><time>number of seconds since midnight</time><date>YYYY/MM/DD</date></from>
        <to><time>number of seconds since midnight</time><day>number of days after from. 1 = next day, 2 = 2 days later, ...</day></to>
        </schedule>
    """
    def __init__(self):
        super().__init__()
        self.start_time_offset = None
        self.start_date = None
        self.end_time_offset = None
        self.end_date = None
        self.duration_hours = None
        self.duration_minutes = None
        self.duration_seconds = None

    def parse_schedule(self, schedule_xml):
        root = ET.fromstring(schedule_xml)
        from_elem = root.find('from')
        to_elem = root.find('to')
        try:
            if from_elem is not None:
                time_elem = from_elem.find('time')
                date_elem = from_elem.find('date')
                if time_elem is not None:
                    self.start_time_offset = int(time_elem.text)
                if date_elem is not None:
                    self.start_date = date_elem.text
            if to_elem is not None:
                time_elem = to_elem.find('time')
                date_elem = to_elem.find('date')
                if time_elem is not None:
                    self.end_time_offset = int(time_elem.text)
                if date_elem is not None:
                    self.end_date = date_elem.text
        except ValueError:
            self.start_time_offset = None
            self.start_date = None
            self.end_time_offset = None
            self.end_date = None

    def get_schedule(self) -> str:
        schedule_xml = f"""
        <schedule>
            <from><time>{self.start_time_offset}</time><date>{self.start_date}</date></from>
            <to><time>{self.end_time_offset}</time><date>{self.end_date}</date></to>
        </schedule>
        """
        return schedule_xml.strip()
    

class WeeklySchedule(NuCoreSchedule):
    """
    <schedule>
        <daysofweek>
        choose specific days of the week from the following
        <sat /><sun /><mon /><tue /><wed /><thu /><fri />
        </daysofweek>
        <from><time>number of seconds since midnight</time></from>
        <to><time>number of seconds since midnight</time></to>
    </schedule>
    """

    def __init__(self):
        super().__init__()
        self.days_of_week = []
        self.start_time_offset = None
        self.end_time_offset = None

    def parse_schedule(self, schedule_xml):
        root = ET.fromstring(schedule_xml)
        days_elem = root.find('daysofweek')
        if days_elem is not None:
            self.days_of_week = [day.tag for day in days_elem]
        from_elem = root.find('from')
        to_elem = root.find('to')
        try:
            if from_elem is not None:
                time_elem = from_elem.find('time')
                if time_elem is not None:
                    self.start_time_offset = int(time_elem.text)
            if to_elem is not None:
                time_elem = to_elem.find('time')
                if time_elem is not None:
                    self.end_time_offset = int(time_elem.text)
        except ValueError:
            self.start_time_offset = None
            self.end_time_offset = None

    def get_schedule(self) -> str:
        schedule_xml = f"""
        <schedule>
            <daysofweek>
                {''.join(f'<{day} />' for day in self.days_of_week)}
            </daysofweek>
            <from><time>{self.start_time_offset}</time></from>
            <to><time>{self.end_time_offset}</time></to>
        </schedule>
        """
        return schedule_xml.strip() 
    

class WeeklySchedulesForSpecificDates(NuCoreSchedule):
    """
    <schedule>
        <daysofweek>
        choose specific days of the week from the following
        <sat /><sun /><mon /><tue /><wed /><thu /><fri />
        </daysofweek>
        <from><time>number of seconds since midnight</time></from>
        <for><hours>hours</hours><minutes>minutes</minutes><seconds>seconds</seconds></for>
    </schedule>
    """
    def __init__(self):
        super().__init__()
        self.days_of_week = []
        self.start_time_offset = None
        self.end_time_offset = None
        self.duration_hours = None
        self.duration_minutes = None
        self.duration_seconds = None

    def parse_schedule(self, schedule_xml):
        root = ET.fromstring(schedule_xml)
        days_elem = root.find('daysofweek')
        if days_elem is not None:
            self.days_of_week = [day.tag for day in days_elem]
        from_elem = root.find('from')
        to_elem = root.find('to')
        duration_elem = root.find('for')
        try:
            if from_elem is not None:
                time_elem = from_elem.find('time')
                if time_elem is not None:
                    self.start_time_offset = int(time_elem.text)
            if to_elem is not None:
                time_elem = to_elem.find('time')
                if time_elem is not None:
                    self.end_time_offset = int(time_elem.text)
            if duration_elem is not None:
                hours_elem = duration_elem.find('hours')
                minutes_elem = duration_elem.find('minutes')
                seconds_elem = duration_elem.find('seconds')
                if hours_elem is not None:
                    self.duration_hours = int(hours_elem.text)
                if minutes_elem is not None:
                    self.duration_minutes = int(minutes_elem.text)
                if seconds_elem is not None:
                    self.duration_seconds = int(seconds_elem.text)
        except ValueError:
            self.start_time_offset = None
            self.end_time_offset = None
            self.duration_hours = None
            self.duration_minutes = None
            self.duration_seconds = None

    def get_schedule(self) -> str:
        schedule_xml = f"""
        <schedule>
            <daysofweek>
                {''.join(f'<{day} />' for day in self.days_of_week)}
            </daysofweek>
            <from><time>{self.start_time_offset}</time></from>
            <to><time>{self.end_time_offset}</time></to>
            <for>
                <hours>{self.duration_hours}</hours>
                <minutes>{self.duration_minutes}</minutes>
                <seconds>{self.duration_seconds}</seconds>
            </for>
        </schedule>
        """
        return schedule_xml.strip()
import pytest

from unified.routine_compiler import TriggerCompileError, compile_trigger_source


def _if(code: str):
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)
    return compiled["if"][0]


def test_at_time():
    assert _if('if at(time="18:00:00"):\n    device("A").command("DON")')["at"] == {"type": "time", "time": 64800}


def test_at_sunrise_negative_offset():
    cond = _if('if at(sunrise=duration(minute=-10)):\n    device("A").command("DON")')
    assert cond["at"] == {"type": "sunrise", "offsetSec": -600}


def test_weekly_at_days_and_time():
    cond = _if('if weekly_at(days="mon,wed,fri", time="18:00:00"):\n    device("A").command("DON")')
    assert cond["daysofweek"] == {"mon": True, "wed": True, "fri": True}
    assert cond["at"] == {"type": "time", "time": 64800}


def test_weekly_between_with_offset_days():
    cond = _if(
        'if weekly_between(days="tue", from_sunset=duration(minute=-10), to_time="01:00:00", to_day=1):\n'
        '    device("A").command("DON")'
    )
    assert cond["from"] == {"type": "sunset", "offsetSec": -600}
    assert cond["to"] == {"type": "time", "time": 3600, "offsetDays": 1}


def test_weekly_for():
    cond = _if(
        'if weekly_for(days="mon,wed,fri", from_sunrise=duration(minute=30), duration=duration(hour=2)):\n'
        '    device("A").command("DON")'
    )
    assert cond["from"] == {"type": "sunrise", "offsetSec": 1800}
    assert cond["for"] == {"type": "for", "hours": 2}


def test_between():
    cond = _if('if between(from_time="08:00:00", to_time="17:00:00"):\n    device("A").command("DON")')
    assert cond["from"]["type"] == "time" and cond["to"]["type"] == "time"


def test_at_lastruntime_new_capability():
    """lastruntime never existed in v1 -- a genuinely new schedule kind."""
    cond = _if(
        'if at(lastruntime=42, offset=duration(minute=5), daily=True):\n    device("A").command("DON")'
    )
    assert cond["at"] == {"type": "lastruntime", "refid": 42, "offsetSec": 300, "daily": True}


def test_mixing_time_references_rejected():
    with pytest.raises(TriggerCompileError, match="exactly one of"):
        compile_trigger_source(
            name="t",
            trigger_id=None,
            comment=None,
            source='if at(time="18:00:00", sunrise=duration(minute=1)):\n    pass',
        )


def test_weekly_at_requires_days():
    with pytest.raises(TriggerCompileError, match="requires days"):
        compile_trigger_source(
            name="t", trigger_id=None, comment=None, source='if weekly_at(time="18:00:00"):\n    pass'
        )

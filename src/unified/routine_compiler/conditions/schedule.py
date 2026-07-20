"""``schedule`` conditions -- direct upgrade of v1's at/weekly_at/
weekly_between/weekly_for/between, generalized to also cover the schema's
new ``lastruntime`` time-reference kind (a schedule relative to *another
routine's* last-run time -- genuinely new, v1 never had this).

Grammar (same function names/kwargs as v1):
    at(time="HH:MM:SS")
    at(sunrise=duration(minute=-10))
    at(sunset=duration(minute=10), date="YYYY/MM/DD")
    at(lastruntime=<routine_id>, offset=duration(minute=5), daily=True)
    weekly_at(days="mon,wed,fri", time="HH:MM:SS")
    between(from_time="08:00:00", to_time="17:00:00", from_date=.., to_date=.., to_day=<int>)
    weekly_between(days="tue", from_sunset=duration(minute=-10), to_time="01:00:00", to_day=1)
    weekly_for(days="mon,wed,fri", from_sunrise=duration(minute=30), duration=duration(hour=2))

Pick exactly one of time=/sunrise=/sunset=/lastruntime= per time reference,
never combine. ``to_day=`` (an integer day offset, 0=same day) only applies
within a "to" reference (the schema's ``offsetDays``, "to" only).
"""

from __future__ import annotations

import ast
from typing import Any

from ..core import (
    TriggerCompileError,
    call_args,
    days_dict,
    duration_call,
    literal,
    register_condition_call,
)


def _parse_hms(node: ast.expr, label: str) -> int:
    value = literal(node)
    if not isinstance(value, str):
        raise TriggerCompileError(f'{label} must be a "HH:MM:SS" string')
    parts = value.split(":")
    if len(parts) != 3:
        raise TriggerCompileError(f'{label} must be in "HH:MM:SS" format')
    try:
        h, m, s = (int(p) for p in parts)
    except ValueError:
        raise TriggerCompileError(f'{label} must be in "HH:MM:SS" format')
    return h * 3600 + m * 60 + s


def _compile_time_ref(prefix: str, kwargs: dict[str, ast.expr], *, allow_offset_days: bool) -> dict[str, Any]:
    time_key, sunrise_key, sunset_key, lastruntime_key = (
        f"{prefix}time",
        f"{prefix}sunrise",
        f"{prefix}sunset",
        f"{prefix}lastruntime",
    )
    date_key, day_key, offset_key, daily_key = (
        f"{prefix}date",
        f"{prefix}day",
        f"{prefix}offset",
        f"{prefix}daily",
    )

    present = [k for k in (time_key, sunrise_key, sunset_key, lastruntime_key) if k in kwargs]
    if len(present) != 1:
        raise TriggerCompileError(
            f"exactly one of {time_key}=/{sunrise_key}=/{sunset_key}=/{lastruntime_key}= is required"
        )

    out: dict[str, Any] = {}
    if time_key in kwargs:
        out["type"] = "time"
        out["time"] = _parse_hms(kwargs[time_key], time_key)
        if date_key in kwargs:
            out["date"] = literal(kwargs[date_key])
    elif sunrise_key in kwargs or sunset_key in kwargs:
        which = "sunrise" if sunrise_key in kwargs else "sunset"
        out["type"] = which
        out["offsetSec"] = duration_call(kwargs[f"{prefix}{which}"]).total_seconds()
        if date_key in kwargs:
            out["date"] = literal(kwargs[date_key])
    else:
        out["type"] = "lastruntime"
        refid = literal(kwargs[lastruntime_key])
        if isinstance(refid, bool) or not isinstance(refid, int):
            raise TriggerCompileError(f"{lastruntime_key}= must be a routine id (integer, see ROUTINES DATABASE)")
        out["refid"] = refid
        out["offsetSec"] = duration_call(kwargs[offset_key]).total_seconds() if offset_key in kwargs else 0
        if daily_key in kwargs:
            out["daily"] = bool(literal(kwargs[daily_key]))

    if allow_offset_days and day_key in kwargs:
        offset_days = literal(kwargs[day_key])
        if isinstance(offset_days, bool) or not isinstance(offset_days, int):
            raise TriggerCompileError(f"{day_key}= must be an integer day offset")
        out["offsetDays"] = offset_days

    return out


def compile_at(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    return {"type": "schedule", "at": _compile_time_ref("", kwargs, allow_offset_days=False)}


def compile_weekly_at(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    if "days" not in kwargs:
        raise TriggerCompileError("weekly_at(...) requires days=")
    days = literal(kwargs.pop("days"))
    at_ref = _compile_time_ref("", kwargs, allow_offset_days=False)
    return {"type": "schedule", "daysofweek": days_dict(days), "at": at_ref}


def compile_between(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    from_ref = _compile_time_ref("from_", kwargs, allow_offset_days=False)
    to_ref = _compile_time_ref("to_", kwargs, allow_offset_days=True)
    return {"type": "schedule", "from": from_ref, "to": to_ref}


def compile_weekly_between(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    if "days" not in kwargs:
        raise TriggerCompileError("weekly_between(...) requires days=")
    days = literal(kwargs.pop("days"))
    from_ref = _compile_time_ref("from_", kwargs, allow_offset_days=False)
    to_ref = _compile_time_ref("to_", kwargs, allow_offset_days=True)
    return {"type": "schedule", "daysofweek": days_dict(days), "from": from_ref, "to": to_ref}


def compile_weekly_for(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    if "days" not in kwargs:
        raise TriggerCompileError("weekly_for(...) requires days=")
    days = literal(kwargs.pop("days"))
    if "duration" not in kwargs:
        raise TriggerCompileError("weekly_for(...) requires duration=duration(...)")
    dur = duration_call(kwargs.pop("duration"))
    from_ref = _compile_time_ref("from_", kwargs, allow_offset_days=False)

    for_ref: dict[str, Any] = {"type": "for"}
    if dur.hour:
        for_ref["hours"] = dur.hour
    if dur.minute:
        for_ref["minutes"] = dur.minute
    if dur.second:
        for_ref["seconds"] = dur.second
    if len(for_ref) == 1:
        raise TriggerCompileError("weekly_for(...)'s duration must be greater than zero.")

    return {"type": "schedule", "daysofweek": days_dict(days), "from": from_ref, "for": for_ref}


register_condition_call("at", compile_at)
register_condition_call("weekly_at", compile_weekly_at)
register_condition_call("between", compile_between)
register_condition_call("weekly_between", compile_weekly_between)
register_condition_call("weekly_for", compile_weekly_for)

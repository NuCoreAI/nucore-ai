"""``var`` action -- assign a NuCore variable from a literal, another
variable, a live device-property snapshot, or a system value (current
time/date/sunrise-sunset/etc). Scaffolding only -- see
``conditions/var_condition.py``'s docstring for why (no variable-discovery
tool ships this iteration).

Grammar (exactly one of value=/var=/device=+property=+uom=/sysval= per call):
    set_var(id=<n>, type=1, op="=", value=<literal>)
    set_var(id=<n>, type=1, op="+=", var=var_ref(id=<m>, type=1))
    set_var(id=<n>, type=1, op="=", device="<DEVICE_ID>", property="<prop_id>", uom=<uom_id>)
    set_var(id=<n>, type=1, op="=", sysval="CurrentHour")

op= is one of: =, +=, -=, *=, /=, %=, &=, |=, ^=, "random", "init".
sysval= is one of: SecondsSinceStartOfDay, MinutesSinceStartOfDay,
CurrentDayOfYear, CurrentDayOfMonth, CurrentDayOfWeek, CurrentYear,
CurrentMonth, CurrentHour, CurrentMinute, CurrentSecond, SunriseToday,
SunsetToday, SunriseTomorrow, SunsetTomorrow, UnixDateTime.
"""

from __future__ import annotations

import ast
from typing import Any

from ..core import TriggerCompileError, call_args, literal, parse_var_ref, register_action_call

_VAR_OP_MAP = {
    "=": "EQ",
    "+=": "ADD=",
    "-=": "SUB=",
    "*=": "MUL=",
    "/=": "DIV=",
    "%=": "REM=",
    "&=": "AND=",
    "|=": "OR=",
    "^=": "XOR=",
    "random": "RDM=",
    "init": "INIT",
}

_SYSVAL_MAP = {
    "SecondsSinceStartOfDay": 1,
    "MinutesSinceStartOfDay": 2,
    "CurrentDayOfYear": 3,
    "CurrentDayOfMonth": 4,
    "CurrentDayOfWeek": 5,
    "CurrentYear": 6,
    "CurrentMonth": 7,
    "CurrentHour": 8,
    "CurrentMinute": 9,
    "CurrentSecond": 10,
    "SunriseToday": 11,
    "SunsetToday": 12,
    "SunriseTomorrow": 13,
    "SunsetTomorrow": 14,
    "UnixDateTime": 15,
}


def compile_set_var(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    if "id" not in kwargs or "type" not in kwargs or "op" not in kwargs:
        raise TriggerCompileError("set_var(...) requires id=, type=, and op=.")

    var_id = literal(kwargs["id"])
    if isinstance(var_id, bool) or not isinstance(var_id, int):
        raise TriggerCompileError("set_var(...)'s id= must be an integer.")
    var_type = literal(kwargs["type"])
    if var_type not in (1, 2):
        raise TriggerCompileError("set_var(...)'s type= must be 1 (integer variable) or 2 (state variable).")
    op_str = literal(kwargs["op"])
    if op_str not in _VAR_OP_MAP:
        raise TriggerCompileError(f"set_var(...)'s op= must be one of: {', '.join(sorted(_VAR_OP_MAP))}")

    out: dict[str, Any] = {"type": "var", "varType": str(var_type), "id": var_id, "op": _VAR_OP_MAP[op_str]}

    modes_present = [k for k in ("value", "var", "device", "sysval") if k in kwargs]
    if len(modes_present) != 1:
        raise TriggerCompileError(
            "set_var(...) requires exactly one of: value= (a literal), var=var_ref(...) (another variable), "
            "device=+property=+uom= (a live device property snapshot), or sysval=\"<name>\" (a system value)."
        )
    mode = modes_present[0]

    if mode == "value":
        value = literal(kwargs["value"])
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TriggerCompileError("set_var(...)'s value= must be a number.")
        val: dict[str, Any] = {"value": value}
        if "precision" in kwargs:
            val["prec"] = literal(kwargs["precision"])
        out["val"] = val

    elif mode == "var":
        ref_id, ref_type = parse_var_ref(kwargs["var"])
        out["var"] = {"id": ref_id, "type": ref_type}

    elif mode == "device":
        if "property" not in kwargs or "uom" not in kwargs:
            raise TriggerCompileError("set_var(...)'s device= requires property= and uom= too.")
        out["status"] = {
            "id": literal(kwargs["property"]),
            "node": literal(kwargs["device"]),
            "uom": literal(kwargs["uom"]),
        }

    else:  # sysval
        sysval_name = literal(kwargs["sysval"])
        if sysval_name not in _SYSVAL_MAP:
            raise TriggerCompileError(f"set_var(...)'s sysval= must be one of: {', '.join(_SYSVAL_MAP)}")
        out["sysval"] = {"id": _SYSVAL_MAP[sysval_name]}

    return out


register_action_call("set_var", compile_set_var)

"""``var`` action -- assign a NuCore variable from a literal, another
variable, a live device-property snapshot, or a system value (current
time/date/sunrise-sunset/etc). See ``conditions/var_condition.py``'s
docstring for the backing ``IoXWrapper``/``list_variables`` support.

Grammar (exactly one of value=/var=/device=+property=+uom=/sysval= per call
-- except op="init", which takes none of them, see below):
    set_var(id=<n>, type=1, op="=", value=<literal>, precision=<p>)
    set_var(id=<n>, type=1, op="+=", var=var_ref(id=<m>, type=1, precision=<q>))
    set_var(id=<n>, type=1, op="=", device="<DEVICE_ID>", property="<prop_id>", uom=<uom_id>)
    set_var(id=<n>, type=1, op="=", sysval="CurrentHour")
    set_var(id=<n>, type=1, op="init")

op= is one of: =, +=, -=, *=, /=, %=, &=, |=, ^=, "random", "init".
sysval= is one of: SecondsSinceStartOfDay, MinutesSinceStartOfDay,
CurrentDayOfYear, CurrentDayOfMonth, CurrentDayOfWeek, CurrentYear,
CurrentMonth, CurrentHour, CurrentMinute, CurrentSecond, SunriseToday,
SunsetToday, SunriseTomorrow, SunsetTomorrow, UnixDateTime.

Confirmed: `op="init"` restores the variable's value from its stored init
value -- it's a self-contained statement, not a value assignment, so it
takes no value=/var=/device=/sysval= source at all (and none is allowed).

Confirmed: variable values are precision-scaled integers on the wire, same
as device command params -- value=<literal> requires precision= (from
list_variables) so the compiler can scale it correctly; the model never
does that arithmetic itself.
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

    if op_str == "init":
        if modes_present:
            raise TriggerCompileError(
                "set_var(..., op=\"init\") restores the variable from its stored init value and takes no "
                "source -- remove value=/var=/device=/sysval= for this op."
            )
        return out

    if len(modes_present) != 1:
        raise TriggerCompileError(
            "set_var(...) requires exactly one of: value= (a literal), var=var_ref(...) (another variable), "
            "device=+property=+uom= (a live device property snapshot), or sysval=\"<name>\" (a system value)."
        )
    mode = modes_present[0]

    if mode == "value":
        if "precision" not in kwargs:
            raise TriggerCompileError(
                "set_var(..., value=...) requires precision= (the variable's own precision, from "
                "list_variables) so the literal can be scaled correctly."
            )
        value = literal(kwargs["value"])
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TriggerCompileError("set_var(...)'s value= must be a number.")
        precision = literal(kwargs["precision"])
        if isinstance(precision, bool) or not isinstance(precision, int) or precision < 0:
            raise TriggerCompileError("set_var(...)'s precision= must be a non-negative integer.")
        out["val"] = {"value": int(round(value * (10 ** precision))), "prec": precision}

    elif mode == "var":
        if "precision" in kwargs:
            raise TriggerCompileError("set_var(..., var=...) doesn't take precision= -- the referenced variable's own value is used as-is.")
        ref_id, ref_type, _ref_precision = parse_var_ref(kwargs["var"])
        out["var"] = {"id": ref_id, "type": ref_type}

    elif mode == "device":
        if "precision" in kwargs:
            raise TriggerCompileError("set_var(..., device=...) doesn't take precision= -- pass the device property's own uom= instead.")
        if "property" not in kwargs or "uom" not in kwargs:
            raise TriggerCompileError("set_var(...)'s device= requires property= and uom= too.")
        out["status"] = {
            "id": literal(kwargs["property"]),
            "node": literal(kwargs["device"]),
            "uom": literal(kwargs["uom"]),
        }

    else:  # sysval
        if "precision" in kwargs:
            raise TriggerCompileError("set_var(..., sysval=...) doesn't take precision=.")
        sysval_name = literal(kwargs["sysval"])
        if sysval_name not in _SYSVAL_MAP:
            raise TriggerCompileError(f"set_var(...)'s sysval= must be one of: {', '.join(_SYSVAL_MAP)}")
        out["sysval"] = {"id": _SYSVAL_MAP[sysval_name]}

    return out


register_action_call("set_var", compile_set_var)

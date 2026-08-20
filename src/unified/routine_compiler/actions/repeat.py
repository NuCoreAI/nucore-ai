"""``repeat`` action -- a repeat-count or repeat-interval marker.

Real schema constraint (confirmed, not guessed): ``Repeat`` has no field for
the actions being repeated -- ``{type:'repeat', for?, every?, while?}`` is
config only. **Everything textually after a ``repeat(...)``/``every(...)``
statement, within the same then/else action list, is what gets repeated** --
this is a flat marker statement, not a Python ``for`` loop wrapping an
indented body (there's nowhere in the schema to put that body). This is a
deliberate departure from v1's ``for _ in repeat(...): <body>`` syntax,
forced by the real schema shape.

Grammar:
    repeat(count=<n>)
    repeat(count=<n>, random=True)
    every(duration(hour=<H>, minute=<M>, second=<S>))
    while_repeat(id=<var_id>, type=1, op=">", value=<n>, precision=<p>)

Confirmed: variable values are precision-scaled integers on the wire, same
as device command params -- precision= is required (from list_variables) so
value= can be scaled correctly; the model never does that arithmetic
itself.

``while_repeat`` compares a NuCore variable to a plain literal only (not
another variable) -- the schema's ``WhileConditionVar`` var-vs-var case is a
rare enough sub-case to skip.
"""

from __future__ import annotations

import ast
from typing import Any

from ..core import (
    COMPARE_STR_TOKENS,
    TriggerCompileError,
    call_args,
    duration_call,
    literal,
    register_action_call,
)


def compile_repeat_count(expr: ast.Call) -> dict[str, Any]:
    args, kwargs = call_args(expr)
    count_node = kwargs.get("count", args[0] if args else None)
    if count_node is None:
        raise TriggerCompileError("repeat(...) requires count=.")
    count = literal(count_node)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise TriggerCompileError("repeat(...)'s count= must be a positive integer.")

    out: dict[str, Any] = {"times": count}
    random_node = kwargs.get("random")
    if random_node is not None and literal(random_node):
        out["random"] = True

    return {"type": "repeat", "for": out}


def compile_repeat_every(expr: ast.Call) -> dict[str, Any]:
    args, kwargs = call_args(expr)
    dur_node = kwargs.get("duration", args[0] if args else None)
    if dur_node is None:
        raise TriggerCompileError("every(...) requires a duration(...) argument.")
    dur = duration_call(dur_node)

    out: dict[str, Any] = {}
    if dur.hour:
        out["hours"] = dur.hour
    if dur.minute:
        out["minutes"] = dur.minute
    if dur.second:
        out["seconds"] = dur.second
    if not out:
        raise TriggerCompileError("every(duration(...))'s duration must be greater than zero.")

    return {"type": "repeat", "every": out}


def compile_while_repeat(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    required = {"id", "type", "op", "value", "precision"}
    missing = required - set(kwargs)
    if missing:
        raise TriggerCompileError(f"while_repeat(...) is missing required argument(s): {', '.join(sorted(missing))}")

    var_id = literal(kwargs["id"])
    if isinstance(var_id, bool) or not isinstance(var_id, int):
        raise TriggerCompileError("while_repeat(...)'s id= must be an integer.")
    var_type = literal(kwargs["type"])
    if var_type not in (1, 2):
        raise TriggerCompileError("while_repeat(...)'s type= must be 1 (integer variable) or 2 (state variable).")
    op_str = literal(kwargs["op"])
    if op_str not in COMPARE_STR_TOKENS:
        raise TriggerCompileError(f"while_repeat(...)'s op= must be one of: {', '.join(COMPARE_STR_TOKENS)}")
    value = literal(kwargs["value"])
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TriggerCompileError("while_repeat(...)'s value= must be a number.")
    precision = literal(kwargs["precision"])
    if isinstance(precision, bool) or not isinstance(precision, int) or precision < 0:
        raise TriggerCompileError("while_repeat(...)'s precision= must be a non-negative integer.")

    val: dict[str, Any] = {"value": int(round(value * (10 ** precision))), "prec": precision}

    return {
        "type": "repeat",
        "while": {"var": {"op": COMPARE_STR_TOKENS[op_str], "varType": str(var_type), "id": var_id, "val": val}},
    }


register_action_call("repeat", compile_repeat_count)
register_action_call("every", compile_repeat_every)
register_action_call("while_repeat", compile_while_repeat)

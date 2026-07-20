"""``x10`` condition -- legacy X10 house/unit/command-code event. Scaffolding
only, per explicit scope decision: this repo has no domain concept of X10
devices anywhere (confirmed via exhaustive grep) and no discovery tool ships
this iteration -- the compiler must correctly parse/compile this shape, but
the model has no way to discover real house/unit/command codes on its own;
the tool description instructs it to ask the customer for the raw codes
rather than guess.

Grammar:
    x10_event(house="B", command=2)
    x10_event(house="B", unit=3, command=2, eq="is")
"""

from __future__ import annotations

import ast
from typing import Any

from ..core import (
    TriggerCompileError,
    call_args,
    literal,
    parse_x10_command,
    parse_x10_house,
    parse_x10_unit,
    register_condition_call,
)

_EQ_VALUES = {"is": "IS", "isnot": "ISNOT"}


def compile_x10_condition(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    missing = {"house", "command"} - set(kwargs)
    if missing:
        raise TriggerCompileError(f"x10_event(...) is missing required argument(s): {', '.join(sorted(missing))}")

    out: dict[str, Any] = {
        "type": "x10",
        "hc": parse_x10_house(kwargs["house"], "x10_event(...)"),
        "cc": parse_x10_command(kwargs["command"], "x10_event(...)"),
    }
    if "unit" in kwargs:
        out["uc"] = parse_x10_unit(kwargs["unit"], "x10_event(...)")

    eq = literal(kwargs["eq"]) if "eq" in kwargs else "is"
    if eq not in _EQ_VALUES:
        raise TriggerCompileError("x10_event(...)'s eq= must be 'is' or 'isnot'.")
    out["op"] = _EQ_VALUES[eq]

    return out


register_condition_call("x10_event", compile_x10_condition)

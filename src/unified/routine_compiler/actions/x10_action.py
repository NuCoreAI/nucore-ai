"""``x10`` action -- send a legacy X10 command. Scaffolding only, same
rationale as ``conditions/x10_condition.py``.

Grammar:
    x10_send(house="B", command=2)
    x10_send(house="B", unit=3, command=2)
"""

from __future__ import annotations

import ast
from typing import Any

from ..core import (
    TriggerCompileError,
    call_args,
    parse_x10_command,
    parse_x10_house,
    parse_x10_unit,
    register_action_call,
)


def compile_x10_action(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    missing = {"house", "command"} - set(kwargs)
    if missing:
        raise TriggerCompileError(f"x10_send(...) is missing required argument(s): {', '.join(sorted(missing))}")

    out: dict[str, Any] = {
        "type": "x10",
        "hc": parse_x10_house(kwargs["house"], "x10_send(...)"),
        "cc": parse_x10_command(kwargs["command"], "x10_send(...)"),
    }
    if "unit" in kwargs:
        out["uc"] = parse_x10_unit(kwargs["unit"], "x10_send(...)")

    return out


register_action_call("x10_send", compile_x10_action)

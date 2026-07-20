"""``sys`` (2 fixed system commands) and ``device`` (query-all broadcast)
actions.

Grammar:
    restart_hub()               # sys, cmd=1
    demand_price_alert()        # sys, cmd=17
    query_all(group="<group_id>")                     # control defaults to "ST"
    query_all(group="<group_id>", property="<prop_id>")
"""

from __future__ import annotations

import ast
from typing import Any

from ..core import TriggerCompileError, call_args, literal, register_action_call


def compile_restart_hub(expr: ast.Call) -> dict[str, Any]:
    args, kwargs = call_args(expr)
    if args or kwargs:
        raise TriggerCompileError("restart_hub() takes no arguments.")
    return {"type": "sys", "cmd": 1}


def compile_demand_price_alert(expr: ast.Call) -> dict[str, Any]:
    args, kwargs = call_args(expr)
    if args or kwargs:
        raise TriggerCompileError("demand_price_alert() takes no arguments.")
    return {"type": "sys", "cmd": 17}


def compile_query_all(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    if "group" not in kwargs:
        raise TriggerCompileError("query_all(...) requires group=.")
    group = literal(kwargs["group"])
    control = literal(kwargs["property"]) if "property" in kwargs else "ST"
    return {"type": "device", "group": group, "control": control}


register_action_call("restart_hub", compile_restart_hub)
register_action_call("demand_price_alert", compile_demand_price_alert)
register_action_call("query_all", compile_query_all)

"""``cmd`` (send a command) and ``wait`` actions -- direct upgrades of v1's
send-command/wait actions.

Grammar:
    device("<DEVICE_ID>").command("<command_id>")
    device("<DEVICE_ID>").command("<command_id>", params=[param(id="<param_id>", value=<v>, uom=<uom_id>, precision=<precision>), ...])
    wait(duration(hour=<H>, minute=<M>, second=<S>))
    wait(duration(...), random=True)
"""

from __future__ import annotations

import ast
from typing import Any

from ..core import (
    TriggerCompileError,
    call_args,
    compile_param_list,
    duration_call,
    literal,
    register_action_call,
    register_action_device_method,
)


def compile_cmd_action(device_id: str, args: list[ast.expr], kwargs: dict[str, ast.expr]) -> dict[str, Any]:
    command_node = kwargs.get("command", args[0] if args else None)
    if command_node is None:
        raise TriggerCompileError("device(...).command(command_id, ...) requires a command id.")
    command = literal(command_node)

    params = compile_param_list(kwargs.get("params"))
    return {"type": "cmd", "id": command, "node": device_id, "p": params}


def compile_wait_action(expr: ast.Call) -> dict[str, Any]:
    args, kwargs = call_args(expr)
    duration_node = kwargs.get("duration", args[0] if args else None)
    if duration_node is None:
        raise TriggerCompileError("wait(...) requires a duration(...) argument.")
    dur = duration_call(duration_node)

    out: dict[str, Any] = {"type": "wait"}
    if dur.hour:
        out["hours"] = dur.hour
    if dur.minute:
        out["minutes"] = dur.minute
    if dur.second:
        out["seconds"] = dur.second

    random_node = kwargs.get("random")
    if random_node is not None and literal(random_node):
        out["random"] = True

    return out


register_action_device_method("command", compile_cmd_action)
register_action_call("wait", compile_wait_action)

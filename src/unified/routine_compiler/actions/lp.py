"""``lp`` (AdjustScene) action -- adjust one responder's link behavior
within a group/scene. Structurally the same operation as the existing
``group_scene_op`` tool's ``update_link`` -- reuses the same group/scene/
device id space (real ids from DEVICE DATABASE), no new lookup needed.

Grammar:
    adjust_scene(group="<scene_id>", controller="<ctlId>", node="<responder_id>", type="cmd"|"default"|"ignore")
    adjust_scene(group="<scene_id>", controller="<ctlId>", node="<responder_id>", type="cmd",
                 command="<command_id>", params=[param(id=.., value=.., uom=.., precision=..), ...])

``controller`` can be a device id or the scene's own id ("Controller: Can be
a node, or the scene itself", per the schema). ``params=`` is only valid
alongside ``command=`` (and only meaningful when ``type="cmd"``).
"""

from __future__ import annotations

import ast
from typing import Any

from ..core import TriggerCompileError, call_args, compile_param_list, literal, register_action_call

_ADJUST_SCENE_TYPES = {"cmd", "default", "ignore"}


def compile_adjust_scene(expr: ast.Call) -> dict[str, Any]:
    _, kwargs = call_args(expr)
    required = {"group", "controller", "node", "type"}
    missing = required - set(kwargs)
    if missing:
        raise TriggerCompileError(f"adjust_scene(...) is missing required argument(s): {', '.join(sorted(missing))}")

    group = literal(kwargs["group"])
    controller = literal(kwargs["controller"])
    node = literal(kwargs["node"])
    rsp_type = literal(kwargs["type"])
    if rsp_type not in _ADJUST_SCENE_TYPES:
        raise TriggerCompileError(f"adjust_scene(...)'s type= must be one of: {', '.join(sorted(_ADJUST_SCENE_TYPES))}")

    rsp: dict[str, Any] = {"type": rsp_type, "node": node}
    if "command" in kwargs:
        rsp["cmd"] = {"cmdId": literal(kwargs["command"])}
        if "params" in kwargs:
            rsp["cmd"]["p"] = compile_param_list(kwargs["params"])
    elif "params" in kwargs:
        raise TriggerCompileError("adjust_scene(...)'s params= requires command= too.")

    return {"type": "lp", "group": group, "ctlId": controller, "rsp": rsp}


register_action_call("adjust_scene", compile_adjust_scene)

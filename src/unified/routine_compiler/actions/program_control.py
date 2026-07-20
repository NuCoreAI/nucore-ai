"""Program-control actions -- a routine controlling *another* routine as a
``then``/``else`` side effect. Reuses the existing ROUTINES DATABASE id
space -- no new lookup needed. These are the same 8 lifecycle operations
``routine_status_op`` already exposes as directly-invoked tool operations
(``runIf``/``runThen``/``runElse``/``stop``/``enable``/``disable``/
``enableRunAtStartup``/``disableRunAtStartup``) -- here reachable from
inside another routine's own logic instead.

Grammar:
    run_if(<routine_id>)
    run_then(<routine_id>)
    run_else(<routine_id>)
    enable_routine(<routine_id>)
    disable_routine(<routine_id>)
    stop_routine(<routine_id>)
    enable_run_at_startup(<routine_id>)
    disable_run_at_startup(<routine_id>)
"""

from __future__ import annotations

import ast
from typing import Any, Callable

from ..core import TriggerCompileError, call_args, literal, register_action_call


def _make_program_ref_action(schema_type: str, dsl_name: str) -> Callable[[ast.Call], dict[str, Any]]:
    def compiler(expr: ast.Call) -> dict[str, Any]:
        args, kwargs = call_args(expr)
        id_node = kwargs.get("id", args[0] if args else None)
        if id_node is None:
            raise TriggerCompileError(f"{dsl_name}(<routine_id>) requires a routine id.")
        refid = literal(id_node)
        if isinstance(refid, bool) or not isinstance(refid, int):
            raise TriggerCompileError(f"{dsl_name}(...)'s routine id must be an integer, exactly as shown in ROUTINES DATABASE.")
        return {"type": schema_type, "id": refid}

    return compiler


_ACTIONS = {
    "run_if": "runif",
    "run_then": "runthen",
    "run_else": "runelse",
    "enable_routine": "enable",
    "disable_routine": "disable",
    "stop_routine": "stop",
    "enable_run_at_startup": "rebootrun",
    "disable_run_at_startup": "rebootnotrun",
}

for _dsl_name, _schema_type in _ACTIONS.items():
    register_action_call(_dsl_name, _make_program_ref_action(_schema_type, _dsl_name))

"""``triggerref`` condition -- "is another routine's if-check currently
true/false". Reuses the existing ROUTINES DATABASE id space (real routine
ids are already listed there) -- no new lookup needed.

Grammar:
    routine_is_true(<routine_id>)
    routine_is_false(<routine_id>)
"""

from __future__ import annotations

import ast
from typing import Any

from ..core import TriggerCompileError, call_args, literal, register_condition_call


def _routine_id(expr: ast.Call, label: str) -> int:
    args, kwargs = call_args(expr)
    id_node = kwargs.get("id", args[0] if args else None)
    if id_node is None:
        raise TriggerCompileError(f"{label}(<routine_id>) requires a routine id.")
    refid = literal(id_node)
    if isinstance(refid, bool) or not isinstance(refid, int):
        raise TriggerCompileError(f"{label}(...)'s routine id must be an integer, exactly as shown in ROUTINES DATABASE.")
    return refid


def compile_routine_is_true(expr: ast.Call) -> dict[str, Any]:
    return {"type": "triggerref", "refid": _routine_id(expr, "routine_is_true"), "is": True}


def compile_routine_is_false(expr: ast.Call) -> dict[str, Any]:
    return {"type": "triggerref", "refid": _routine_id(expr, "routine_is_false"), "is": False}


register_condition_call("routine_is_true", compile_routine_is_true)
register_condition_call("routine_is_false", compile_routine_is_false)

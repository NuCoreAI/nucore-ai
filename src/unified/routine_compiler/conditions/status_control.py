"""``status`` (property-state comparison) and ``control`` (physical
control-event) conditions -- direct upgrades of v1's COS/COC.

Grammar:
    device("<DEVICE_ID>").status("<property_id>", uom=<uom_id>, precision=<precision>) <comp> <value>
    device("<DEVICE_ID>").was_controlled(command="<command_id>", eq="is"|"isnot")

Real behavior reduction vs. v1, forced by the new schema: the new ``Control``
type has only ``id``/``node``/``op`` -- no ``parameters`` field at all, unlike
v1's ``was_controlled(..., params=[param(...)])``. So the v2 DSL's
``was_controlled`` no longer accepts ``params=``.
"""

from __future__ import annotations

import ast
from typing import Any

from ..core import (
    TriggerCompileError,
    call_args,
    compare_op_token,
    literal,
    register_compare_compiler,
    register_condition_device_method,
    scale_value,
)

_EQ_VALUES = {"is": "IS", "isnot": "ISNOT"}


def compile_status_condition(expr: ast.Compare) -> dict[str, Any] | None:
    left = expr.left
    if not (isinstance(left, ast.Call) and isinstance(left.func, ast.Attribute) and left.func.attr == "status"):
        return None  # not a status comparison -- let other registered compare compilers (e.g. var) try

    if len(expr.ops) != 1 or len(expr.comparators) != 1:
        raise TriggerCompileError("Chained comparisons (e.g. a < b < c) are not supported; write one comparison per condition.")

    device_call = left.func.value
    if not (isinstance(device_call, ast.Call) and isinstance(device_call.func, ast.Name) and device_call.func.id == "device"):
        raise TriggerCompileError("device(...).status(...) must be called on a device(...) reference.")
    dargs, dkwargs = call_args(device_call)
    device_id_node = dkwargs.get("id", dargs[0] if dargs else None)
    if device_id_node is None:
        raise TriggerCompileError("device(...) requires a device/group id string")
    device_id = str(literal(device_id_node))

    args, kwargs = call_args(left)
    prop_id_node = kwargs.get("property", args[0] if args else None)
    if prop_id_node is None:
        raise TriggerCompileError("device(...).status(property_id, uom=..., precision=...) requires a property id.")
    prop_id = str(literal(prop_id_node))

    if "uom" not in kwargs or "precision" not in kwargs:
        raise TriggerCompileError("status(...) requires uom and precision keyword arguments.")
    uom = literal(kwargs["uom"])
    precision = literal(kwargs["precision"])

    op = compare_op_token(expr.ops[0])

    rhs = expr.comparators[0]
    value = literal(rhs)
    scaled = scale_value(value, uom, precision)

    # uom must be a schema-typed number (trigger-new.json) regardless of what
    # literal type the model wrote -- see compile_cmd_param's comment for why
    # this is a real, not hypothetical, risk.
    return {
        "type": "status",
        "id": prop_id,
        "node": device_id,
        "op": op,
        "val": {"value": scaled, "prec": int(precision), "uom": int(uom)},
    }


def compile_control_condition(device_id: str, args: list[ast.expr], kwargs: dict[str, ast.expr]) -> dict[str, Any]:
    command_node = kwargs.get("command", args[0] if args else None)
    if command_node is None:
        raise TriggerCompileError("was_controlled(command=..., ...) requires a command id.")
    command = literal(command_node)

    if "params" in kwargs:
        raise TriggerCompileError(
            "was_controlled(...) does not accept params= in this schema -- the control condition can only "
            "check whether the command was sent, not a specific parameter value."
        )

    eq_node = kwargs.get("eq")
    eq = literal(eq_node) if eq_node is not None else "is"
    if eq not in _EQ_VALUES:
        raise TriggerCompileError("eq must be 'is' or 'isnot'.")

    return {"type": "control", "id": command, "node": device_id, "op": _EQ_VALUES[eq]}


register_compare_compiler(compile_status_condition)
register_condition_device_method("was_controlled", compile_control_condition)

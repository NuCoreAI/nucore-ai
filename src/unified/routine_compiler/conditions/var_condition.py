"""``var`` condition -- compares a NuCore variable against a literal or
another variable. Scaffolding only, per explicit scope decision: this repo
has no domain concept of "which variables exist" anywhere (confirmed via
exhaustive grep) and no discovery tool ships this iteration -- the compiler
must correctly parse/compile this shape (so an existing hub program using
variables round-trips, and a customer who states a real variable id/type
explicitly can be accommodated), but the model has no way to discover ids
on its own; the tool description instructs it to ask the customer rather
than guess.

Grammar:
    var_ref(id=<n>, type=1)                      # type: 1=integer variable, 2=state variable
    var_ref(id=<n>, type=1) > 10                  # compare to a literal
    var_ref(id=<n>, type=1) == var_ref(id=<m>, type=2)   # compare to another variable
"""

from __future__ import annotations

import ast
from typing import Any

from ..core import (
    TriggerCompileError,
    compare_op_token,
    literal,
    parse_var_ref,
    register_compare_compiler,
)


def compile_var_condition(expr: ast.Compare) -> dict[str, Any] | None:
    left = expr.left
    if not (isinstance(left, ast.Call) and isinstance(left.func, ast.Name) and left.func.id == "var_ref"):
        return None  # not a var comparison -- let other registered compare compilers try

    if len(expr.ops) != 1 or len(expr.comparators) != 1:
        raise TriggerCompileError("Chained comparisons (e.g. a < b < c) are not supported; write one comparison per condition.")

    var_id, var_type = parse_var_ref(left)
    op = compare_op_token(expr.ops[0])

    rhs = expr.comparators[0]
    out: dict[str, Any] = {"type": "var", "id": var_id, "varType": var_type, "op": op}
    if isinstance(rhs, ast.Call) and isinstance(rhs.func, ast.Name) and rhs.func.id == "var_ref":
        rhs_id, rhs_type = parse_var_ref(rhs)
        out["var"] = {"id": rhs_id, "type": rhs_type}
    else:
        value = literal(rhs)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TriggerCompileError("var_ref(...) comparison value must be a number or another var_ref(...).")
        out["val"] = {"value": value}

    return out


register_compare_compiler(compile_var_condition)

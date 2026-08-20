"""``var`` condition -- compares a NuCore variable against a literal or
another variable. Backed by full ``IoXWrapper``/``NuCoreInterface`` support
(``_load_variables``, ``variable_ops``) and the ``list_variables`` tool, which
the model calls on demand to discover real ids/types/precisions -- no
standing prompt database (variables are rare enough not to justify the
per-turn token cost every device/routine already pays for).

Grammar:
    var_ref(id=<n>, type=1, precision=<p>)                      # type: 1=integer variable, 2=state variable
    var_ref(id=<n>, type=1, precision=<p>) > 10                  # compare to a literal -- scaled by precision=
    var_ref(id=<n>, type=1, precision=<p>) == var_ref(id=<m>, type=2, precision=<q>)   # compare to another variable

Confirmed: variable values are always precision-scaled integers on the wire
(same ``raw * 10**prec`` convention as device command params) -- so, same as
``param()``, ``precision=`` is required and the compiler does the scaling
math itself; the model never does it. ``precision=`` comes from
``list_variables``.
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

    var_id, var_type, precision = parse_var_ref(left)
    op = compare_op_token(expr.ops[0])

    rhs = expr.comparators[0]
    out: dict[str, Any] = {"type": "var", "id": var_id, "varType": var_type, "op": op}
    if isinstance(rhs, ast.Call) and isinstance(rhs.func, ast.Name) and rhs.func.id == "var_ref":
        rhs_id, rhs_type, _rhs_precision = parse_var_ref(rhs)  # rhs's own precision required but unused here -- the hub compares raw values natively
        out["var"] = {"id": rhs_id, "type": rhs_type}
    else:
        value = literal(rhs)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TriggerCompileError("var_ref(...) comparison value must be a number or another var_ref(...).")
        out["val"] = {"value": int(round(value * (10 ** precision))), "prec": precision}

    return out


register_compare_compiler(compile_var_condition)

"""``list_variables``/``variable_op`` -- NuCore variables (integer counters
or state flags). Deliberately NOT a standing prompt database like DEVICE/
ROUTINES DATABASE -- variables are a narrow feature relative to devices/
routines, so paying their token cost on every single turn (even ones that
never touch a variable) isn't worth it. ``list_variables`` is the on-demand
equivalent of ``get_device_detail``: called only on the turns that actually
need real id/type/precision/value, e.g. before authoring
``var_ref()``/``set_var()``/``while_repeat()`` in ``create_or_update_routine``
(see ``unified.routine_compiler``), or when the customer asks about a
variable directly. ``variable_op`` manages the variable itself (existence,
name, precision, stored/init value) -- the same content-vs-runtime-state
split already used for routines (``create_or_update_routine`` vs.
``routine_status_op``), just narrower since there's no separate "logic" to
edit.
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface


def _op_ok(result: Any) -> bool:
    """``variable_ops`` returns a ``requests.Response`` of *any* status code
    on a REST call, or ``None`` on a bad call -- never raises on a hub-side
    rejection, so ``result is not None`` alone is not a valid success check
    (same class of bug already fixed in node_ops.py/routine_status_ops.py)."""
    if result is None or result is False or isinstance(result, str):
        return False
    status_code = getattr(result, "status_code", None)
    return status_code is not None and 200 <= status_code < 300


def _op_error(result: Any) -> str:
    if result is None or result is False:
        return "no response from backend"
    if isinstance(result, str):
        return result
    status_code = getattr(result, "status_code", None)
    return f"HTTP {status_code}" if status_code is not None else str(result)


def _extract_created_id(result: Any) -> str | None:
    """The create response carries the hub-assigned id directly -- unlike
    routine creation, no refresh-and-search-by-name fallback is needed."""
    json_method = getattr(result, "json", None)
    if not callable(json_method):
        return None
    try:
        body = json_method()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    data = body.get("data", body)
    var_id = data.get("id") if isinstance(data, dict) else data
    return str(var_id) if var_id is not None else None


async def list_variables(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    var_type = args.get("type")
    if var_type is not None and var_type not in (1, 2):
        return {"error": "type must be 1 (integer variable) or 2 (state variable) if given"}

    # Best-effort refresh so a variable_op write earlier in this same turn
    # (which only flips routines_changed, it doesn't refetch immediately)
    # is reflected here rather than only on the next turn's prompt build.
    try:
        await nucore_interface._refresh_routines_database()
    except Exception:
        pass

    variables = nucore_interface.condensed_variables
    if var_type is not None:
        variables = [v for v in variables if v.get("type") == var_type]
    return {"variables": variables}


async def variable_op(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    var_type = args.get("type")
    operation = args.get("operation")
    var_id = args.get("id")

    if var_type not in (1, 2):
        return {"error": "type is required and must be 1 (integer variable) or 2 (state variable)"}
    if operation not in ("create", "update", "delete"):
        return {"error": "operation is required and must be one of: create, update, delete"}
    if operation != "create" and not var_id:
        return {"error": f"id is required for '{operation}'"}

    kwargs = {k: args[k] for k in ("name", "prec", "value", "init") if args.get(k) is not None}

    try:
        result = await nucore_interface.variable_ops(
            var_type, str(var_id) if var_id is not None else None, operation, **kwargs
        )
    except Exception as exc:
        return {"error": f"failed to {operation} variable: {exc}"}

    if not _op_ok(result):
        return {"error": f"'{operation}' failed for variable type {var_type}: {_op_error(result)}"}

    # Reuses the shared refresh cycle (routines + variables load together --
    # see NuCoreInterface._refresh_routines_database) so the next prompt
    # build picks up fresh variable state, same as routine writes already do.
    nucore_interface.routines_changed = True

    if operation == "create":
        new_id = _extract_created_id(result)
        if new_id is None:
            return {
                "type": var_type,
                "status": "saved",
                "warning": "variable was created but its new id could not be read from the response",
            }
        return {"type": var_type, "id": new_id, "status": "saved"}

    if operation == "update":
        return {"type": var_type, "id": var_id, "status": "saved"}

    return {"type": var_type, "id": var_id, "status": "ok"}  # delete

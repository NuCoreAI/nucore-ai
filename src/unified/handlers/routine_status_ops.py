"""``routine_status_op`` -- operates on an explicit routine id, no name
resolution needed.

Fresh dispatch calling directly into ``NuCoreInterface.routine_ops`` --
does not import or invoke ``intent_handler_directory/routine_status_ops``.
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface


def _op_ok(result: Any) -> bool:
    """``routine_ops`` returns a ``requests.Response`` of *any* status code
    on a REST call, or ``None`` only for a falsy id/unrecognised operation --
    it never raises on a hub-side rejection, so ``result is not None`` alone
    is not a valid success check (same class of bug already fixed in
    node_ops.py and routine_automation.py)."""
    if result is None or isinstance(result, str):
        return False
    status_code = getattr(result, "status_code", None)
    return status_code is not None and 200 <= status_code < 300


def _op_error(result: Any) -> str:
    if result is None:
        return "no response from backend"
    if isinstance(result, str):
        return result
    status_code = getattr(result, "status_code", None)
    return f"HTTP {status_code}" if status_code is not None else str(result)


async def routine_status_op(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    routine_id = args.get("id")
    operation = args.get("operation")
    if not routine_id or not operation:
        return {"error": "id and operation are both required"}

    result = await nucore_interface.routine_ops(routine_id, operation)
    if not _op_ok(result):
        return {"error": f"'{operation}' failed for routine {routine_id}: {_op_error(result)}"}
    return {"id": routine_id, "operation": operation, "status": "ok"}

"""``routine_status_op`` -- operates on an explicit routine id, no name
resolution needed.

Fresh dispatch calling directly into ``NuCoreInterface.routine_ops`` --
does not import or invoke ``intent_handler_directory/routine_status_ops``.
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface


async def routine_status_op(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    routine_id = args.get("id")
    operation = args.get("operation")
    if not routine_id or not operation:
        return {"error": "id and operation are both required"}

    result = await nucore_interface.routine_ops(routine_id, operation)
    return {"id": routine_id, "operation": operation, "status": "ok" if result is not None else "failed"}

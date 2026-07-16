"""``node_op`` -- operates on explicit node ids, no name resolution needed.

Fresh dispatch calling directly into ``NuCoreInterface.add_node``/``node_ops``
-- does not import or invoke ``intent_handler_directory/node_ops``.
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface

_CREATE_OPS = {"add_group", "add_folder"}
_SIMPLE_OPS = {"enable", "disable", "delete"}


async def node_op(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    operation = args.get("operation")

    if operation in _CREATE_OPS:
        new_name = args.get("new_name")
        if not new_name:
            return {"error": f"'{operation}' requires new_name"}
        node_type = "group" if operation == "add_group" else "folder"
        result = await nucore_interface.add_node(node_name=new_name, type=node_type)
        return {"operation": operation, "new_name": new_name, "status": "ok" if result is not None else "failed"}

    node_id = args.get("node_id")
    if not node_id:
        return {"error": f"node_id is required for operation '{operation}'"}

    kwargs: dict[str, Any] = {}
    if operation == "rename":
        new_name = args.get("new_name")
        if not new_name:
            return {"error": "rename requires new_name"}
        kwargs["new_name"] = new_name
    elif operation == "move":
        new_parent_id = args.get("new_parent_id")
        if not new_parent_id:
            return {"error": "move requires new_parent_id"}
        kwargs["new_parent_id"] = new_parent_id
    elif operation not in _SIMPLE_OPS:
        return {"error": f"unknown node_op operation '{operation}'"}

    result = await nucore_interface.node_ops(node_id, operation, **kwargs)
    return {"node_id": node_id, "operation": operation, "status": "ok" if result is not None else "failed"}

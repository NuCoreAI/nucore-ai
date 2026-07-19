"""``node_op`` -- operates on explicit node ids, no name resolution needed.

Fresh dispatch calling directly into ``NuCoreInterface.add_node``/``node_ops``
-- does not import or invoke ``intent_handler_directory/node_ops``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nucore import NuCoreInterface

_CREATE_OPS = {"add_group", "add_folder"}
_SIMPLE_OPS = {"enable", "disable", "delete"}


def _op_ok(result: Any) -> bool:
    """``add_node``/``node_ops`` return a ``requests.Response`` on success,
    but a plain error *string* (not ``None``) on failure (e.g. "Node not
    found: X") -- ``result is not None`` alone is not a valid success check,
    a truthy error string would pass it."""
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


async def node_op(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    operation = args.get("operation")

    if operation in _CREATE_OPS:
        new_name = args.get("new_name")
        if not new_name:
            return {"error": f"'{operation}' requires new_name"}
        node_type = "group" if operation == "add_group" else "folder"
        result = await nucore_interface.add_node(node_name=new_name, type=node_type)
        if not _op_ok(result):
            return {"error": f"failed to create {node_type} '{new_name}': {_op_error(result)}"}

        # add_node's response doesn't carry the new node's id -- look it up
        # by name after a refresh, same pattern as multi_device_scene's
        # newly-created-group lookup.
        await asyncio.sleep(2)
        await nucore_interface._refresh_device_structure()
        registry = nucore_interface.groups if node_type == "group" else nucore_interface.folders
        new_id = next((address for address, node in registry.items() if node.name == new_name), None)
        if new_id is None:
            return {"error": f"created {node_type} '{new_name}' but could not find its id afterward"}
        return {"operation": operation, "new_name": new_name, "node_id": new_id, "status": "ok"}

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
    if not _op_ok(result):
        return {"error": f"'{operation}' failed: {_op_error(result)}"}
    return {"node_id": node_id, "operation": operation, "status": "ok"}

"""``node_op`` -- operates on explicit node ids, no name resolution needed.

Dispatch calling directly into ``NuCoreInterface.add_node``/``node_ops``.
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface

from ._event_wait import wait_until

_CREATE_OPS = {"add_group", "add_folder"}
_SIMPLE_OPS = {"enable", "disable", "delete"}
_CREATE_WAIT_TIMEOUT_S = 15


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
        # by name after the new node's added-event arrives, same pattern as
        # multi_device_scene's newly-created-group lookup.
        def _registry():
            return nucore_interface.groups if node_type == "group" else nucore_interface.folders

        await wait_until(
            nucore_interface, "_3", None,
            lambda: any(node.name == new_name for node in _registry().values()),
            nucore_interface._refresh_device_structure,
            _CREATE_WAIT_TIMEOUT_S,
        )
        new_id = next((address for address, node in _registry().items() if node.name == new_name), None)
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
        # An empty/omitted new_parent_id is not an invalid call -- it means
        # "move to the top level/root", not "no destination given". See
        # IoxWrapper.node_ops's move branch for how that's actually sent.
        kwargs["new_parent_id"] = args.get("new_parent_id") or ""
    elif operation not in _SIMPLE_OPS:
        return {"error": f"unknown node_op operation '{operation}'"}

    result = await nucore_interface.node_ops(node_id, operation, **kwargs)
    if not _op_ok(result):
        return {"error": f"'{operation}' failed: {_op_error(result)}"}
    return {"node_id": node_id, "operation": operation, "status": "ok"}

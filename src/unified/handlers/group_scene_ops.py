"""``group_scene_op`` -- operates on explicit group/member ids, no name
resolution needed.

Fresh dispatch calling directly into ``NuCoreInterface.group_scene_add_member``/
``remove_member``/``update_link`` with their real keyword argument names --
does not import or invoke ``intent_handler_directory/group_scene_ops``
(whose existing call sites have their own, unrelated kwarg-mismatch bugs).

Scope note: this does not reproduce the old handler's client-side
controller/responder role prechecks (``group_scene_get_node_roles``/
``group_scene_get_link_types``) -- that's real, non-trivial interpretation
logic without a clear enough spec to safely re-derive from scratch here.
Validation for this iteration relies on the underlying REST call's own
success/failure signal.
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface


def _result_ok(result: dict[str, Any] | None) -> bool:
    return bool(result) and result.get("successful", False)


async def group_scene_op(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    operation = args.get("operation")
    group_address = args.get("group_address")
    link_address = args.get("link_address")
    if not group_address or not link_address:
        return {"error": "group_address and link_address are both required"}

    if operation == "add_member":
        result = nucore_interface.group_scene_add_member(
            group_address=group_address,
            link_address=link_address,
            is_controller=bool(args.get("is_controller", False)),
            name=args.get("name"),
        )
    elif operation == "remove_member":
        result = nucore_interface.group_scene_remove_member(
            group_address=group_address, link_address=link_address
        )
    elif operation == "update_link":
        link = args.get("link")
        if not isinstance(link, dict):
            return {"error": "update_link requires a 'link' object describing the new behavior"}
        result = nucore_interface.group_scene_update_link(
            group_address=group_address, controller_address=link_address, link=link
        )
    else:
        return {"error": f"unknown group_scene_op operation '{operation}'"}

    if not _result_ok(result):
        return {"error": f"group scene operation failed: {result}"}
    return {"group_address": group_address, "link_address": link_address, "operation": operation, "status": "ok"}

"""``group_scene_op``/``get_group_detail``/``multi_device_scene`` -- operate
on explicit group/member ids, no name resolution needed.

Fresh dispatch calling directly into ``NuCoreInterface``/domain objects
(``group_scene_add_member``/``remove_member``/``update_link``,
``Group.explain_json``, ``add_node``) with their real keyword argument
names -- does not import or invoke ``intent_handler_directory/group_scene_ops``
(whose existing call sites have their own, unrelated kwarg-mismatch bugs,
and whose ``_execute_multi_device_scene`` is a ``BaseIntentHandler`` method,
not reusable backend code -- ``multi_device_scene`` below is a fresh
reimplementation of the same composition of backend calls).

Scope note: ``group_scene_op`` does not reproduce the old handler's
client-side controller/responder role prechecks (``group_scene_get_node_roles``/
``group_scene_get_link_types``) for its single-step operations -- that's
real, non-trivial interpretation logic without a clear enough spec to
safely re-derive from scratch for the general case. ``multi_device_scene``
*does* run the ``availableAsController``/``availableAsResponder`` role
precheck per member, since that composition's shape is well-specified
(unlike the general step-sequencing case).
"""

from __future__ import annotations

import asyncio
from typing import Any

from nucore import Group, NuCoreInterface


def _result_ok(result: dict[str, Any] | None) -> bool:
    return bool(result) and result.get("successful", False)


def _check_role_precheck(nucore_interface: NuCoreInterface, link_address: str, is_controller: bool) -> str | None:
    """Return an error string if *link_address* can't serve the requested
    role, else ``None``. Same precheck the classic handler ran per member
    add via ``group_scene_get_node_roles`` -- reimplemented here directly
    against the backend, not imported from intent_handler_directory."""
    payload = nucore_interface.group_scene_get_node_roles(node_address=link_address)
    if payload is None:
        return f"failed to fetch node roles for '{link_address}'"
    data = payload.get("data", {}) if isinstance(payload, dict) else {}
    if is_controller and not bool(data.get("availableAsController", False)):
        return f"'{link_address}' is not available as a controller"
    if not is_controller and not bool(data.get("availableAsResponder", False)):
        return f"'{link_address}' is not available as a responder"
    return None


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


async def get_group_detail(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    group_address = args.get("group_address")
    if not group_address:
        return {"error": "group_address is required"}

    node = nucore_interface.get_node(group_address)
    if node is None:
        return {"error": f"no group/scene found with id '{group_address}'; check DEVICE DATABASE"}
    if not isinstance(node, Group):
        return {"error": f"'{group_address}' is a device, not a group/scene"}

    return {"name": node.name, "address": node.address, **node.explain_json()}


async def multi_device_scene(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    devices = args.get("devices")
    if not isinstance(devices, list) or not devices:
        return {"error": "'devices' must be a non-empty list"}

    for idx, device in enumerate(devices):
        if not isinstance(device, dict):
            return {"error": f"device entry at index {idx} is not an object"}
        link_address = str(device.get("link_address") or "").strip()
        role = str(device.get("role") or "").strip().lower()
        if not link_address or role not in {"controller", "responder"}:
            return {
                "error": (
                    f"device entry at index {idx}: 'link_address' and a role "
                    "('controller'/'responder') are both required"
                )
            }
        if role == "controller":
            existing = nucore_interface.get_groups_for_device(link_address, controller_only=True)
            if existing:
                names = ", ".join(f"{g.name} [{g.address}]" for g in existing)
                return {"error": f"'{link_address}' is already a controller in: {names}"}

    group_address = args.get("group_address")
    group_name = args.get("group_name")
    if not group_address:
        group_name = group_name or f"NuCore_Scene_{len(nucore_interface.groups) + 1}"
        response = await nucore_interface.add_node(node_name=group_name, type="group")
        if getattr(response, "status_code", None) != 200:
            return {"error": f"failed to create group/scene '{group_name}': {response}"}
        await asyncio.sleep(2)
        await nucore_interface._refresh_device_structure()
        group_address = next(
            (address for address, group in nucore_interface.groups.items() if group.name == group_name),
            None,
        )
        if not group_address:
            return {"error": f"created group/scene '{group_name}' but could not find its address afterward"}

    results: list[dict[str, Any]] = []
    for idx, device in enumerate(devices):
        link_address = str(device["link_address"]).strip()
        role = str(device["role"]).strip().lower()
        is_controller = role == "controller"

        precheck_error = _check_role_precheck(nucore_interface, link_address, is_controller)
        if precheck_error:
            results.append(
                {"index": idx, "link_address": link_address, "role": role, "successful": False, "error": precheck_error}
            )
            continue

        result = nucore_interface.group_scene_add_member(
            group_address=group_address, link_address=link_address, is_controller=is_controller, name=device.get("name")
        )
        ok = _result_ok(result)
        results.append(
            {
                "index": idx,
                "link_address": link_address,
                "role": role,
                "successful": ok,
                **({} if ok else {"error": result}),
            }
        )

    successful = sum(1 for r in results if r["successful"])
    return {
        "group_address": group_address,
        "group_name": group_name,
        "summary": {"total": len(results), "successful": successful, "failed": len(results) - successful},
        "results": results,
    }

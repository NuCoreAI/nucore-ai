"""Diagnostic-adjacent tools: cheap, side-effect-free standing reads
(``get_full_system_config``/``get_core_services_status``/``get_device_family``)
needed too broadly (the "Step 1, always" INSTEON-diagnostics rule; any
protocol-family question) to gate behind a diagnostics-specific round trip --
plus the complaint-shaped investigation tools (``diagnostics_not_responding``/
``diagnostics_no_status_feedback``) and ``restart_core_service``, the one step
orphaned by removing the old generic ``run_diagnostic_step`` dispatcher.

All of these are thin pass-throughs, arg validation only: the actual
investigation behind the complaint-shaped tools (which backend calls to make,
in what order, how to interpret the result) lives entirely in
``NuCoreInterface.diagnose_not_responding``/``diagnose_no_status_feedback``
(backend-owned, e.g. ``IoXDiagnostics`` for IoX-backed installations).
Keeping the orchestration in the backend, not here, is deliberate: it lets
the backend call its own private helpers (link tables, PLM sanity checks)
directly, typed, without promoting any of that internal mechanism onto the
model-facing tool surface or the abstract NuCoreInterface contract.
"""

from __future__ import annotations

from typing import Any

from nucore import Group, NuCoreInterface


async def get_full_system_config(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    return await nucore_interface.get_full_system_config()


async def get_core_services_status(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    return await nucore_interface.get_core_services_status()


async def get_device_family(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    device_id = args.get("device_id")
    if not device_id:
        return {"error": "device_id is required"}
    return await nucore_interface.get_device_family(device_id)


async def diagnostics_not_responding(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    protocol = args.get("protocol")
    if not protocol:
        return {"error": "protocol is required"}
    return await nucore_interface.diagnose_not_responding(protocol, args.get("device_id"), force=bool(args.get("force", False)))


async def diagnostics_no_status_feedback(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    protocol = args.get("protocol")
    if not protocol:
        return {"error": "protocol is required"}
    return await nucore_interface.diagnose_no_status_feedback(protocol, args.get("device_id"))


async def restart_core_service(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    service = args.get("service")
    operation = args.get("operation")
    if not service or not operation:
        return {"error": "service and operation are both required"}
    return await nucore_interface.restart_core_service(service, operation)


async def scene_test(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    group_address = args.get("group_address")
    if not group_address:
        return {"error": "group_address is required"}

    node = nucore_interface.get_node(group_address)
    if not isinstance(node, Group):
        return {"error": f"'{group_address}' is not a scene -- scene_test needs a scene"}

    result = await nucore_interface.scene_test(group_address)
    if not result.get("successful"):
        return {"error": result.get("error") or f"scene test failed for '{group_address}'"}
    response = {
        "group_address": group_address,
        "file_path": result.get("file_path"),
        "event_count": result.get("event_count"),
        "summary": result.get("summary"),
        "details": result.get("details"),
    }
    if "note" in result:
        response["note"] = result["note"]
    return response

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from rag import ProfileRagFormatter

# Real state-change events take a moment to reach the hub's in-memory
# node/routine cache over its websocket subscription -- the existing
# group_scene_ops handler works around the same gap with an explicit
# `time.sleep(2)` before its own post-mutation refresh. Match that here.
_SETTLE_DELAY_S = 1.5


async def _force_refresh_nodes(nucore_interface: Any) -> None:
    """Force a genuinely fresh read of nodes/groups/folders/summary_rags.

    ``_refresh_device_structure()`` only re-fetches when the hub's websocket
    has already flagged ``device_structure_changed`` -- which depends on a
    live event subscription and on-time propagation after any REST mutation.
    Calling ``_load`` directly instead guarantees a true fresh REST read
    regardless of websocket timing/availability, which matters here since
    the harness is diffing before/after state right after issuing its own
    mutating REST calls.
    """
    await asyncio.sleep(_SETTLE_DELAY_S)
    await nucore_interface._load(include_profiles=True)


async def _force_refresh_routines(nucore_interface: Any) -> None:
    """Force a genuinely fresh read of routines/condensed_routines; see ``_force_refresh_nodes``."""
    await asyncio.sleep(_SETTLE_DELAY_S)
    await nucore_interface._load_routines()


def encode(raw_address: str) -> str:
    """Base64-encode a raw IoX address the way the RAG layer does.

    ``get_properties``/``send_commands`` decode their ``device``/``device_id``
    argument assuming it is Base-64 (that's the shape the LLM sees via RAG
    documents); ``node_ops``/``routine_ops``/``get_routine``/``add_node`` take
    the raw address directly. The live inventory (harness/inventory.py) and
    generated queries (harness/query_gen.py) both deal in raw addresses, so
    callers encode only at the call sites that need it.
    """
    return ProfileRagFormatter.encode_id(raw_address)


@dataclass
class DeviceSnapshot:
    raw_address: str
    properties: dict[str, str] = field(default_factory=dict)  # property id -> raw value


@dataclass
class NodeSnapshot:
    raw_address: str
    name: str | None = None
    enabled: bool | None = None
    parent: str | None = None


@dataclass
class RoutineSnapshot:
    routine_id: str | None  # None means the test is expected to create a new routine
    definition: dict[str, Any] | None = None  # full get_routine() payload, when editing an existing one


async def snapshot_device(nucore_interface: Any, raw_address: str) -> DeviceSnapshot:
    """Capture current property values for a device before a test mutates it."""
    properties = await nucore_interface.get_properties(encode(raw_address))
    values = {prop_id: prop.value for prop_id, prop in (properties or {}).items()}
    return DeviceSnapshot(raw_address=raw_address, properties=values)


async def best_effort_restore_property(nucore_interface: Any, snapshot: DeviceSnapshot) -> dict[str, Any]:
    """Best-effort generic restore for a device snapshotted before a fuzzy query.

    There's no hand-authored inverse command for a query the harness didn't
    write itself, so the only generically-safe restore is the common binary
    on/off convention: ``ST`` (status) "0" means off, any other value means
    on, so DOF/DON puts it back. Anything else -- a precise dim level,
    thermostat setpoint, color, lock state -- is deliberately left alone and
    reported as ``restore_uncertain`` rather than guessed at: sending the
    wrong command to "restore" an unfamiliar property risks doing more
    damage than doing nothing, and the before/after values are already in
    the report for a human to fix manually.
    """
    st_before = snapshot.properties.get("ST")
    if st_before is None:
        return {"restored": False, "reason": "no ST property was captured for this device"}

    after = await snapshot_device(nucore_interface, snapshot.raw_address)
    st_after = after.properties.get("ST")
    if st_after == st_before:
        return {"restored": False, "reason": "already matches its pre-test value"}

    try:
        before_on = float(st_before) > 0
    except (TypeError, ValueError):
        return {
            "restored": False,
            "restore_uncertain": True,
            "reason": f"ST value '{st_before}' isn't numeric; not guessing a restore command",
            "before": st_before,
            "after": st_after,
        }

    command = "DON" if before_on else "DOF"
    await nucore_interface._send_commands([{"device": snapshot.raw_address, "command": command}])
    return {"restored": True, "command": command, "before": st_before, "after": st_after}


async def snapshot_node(nucore_interface: Any, raw_address: str) -> NodeSnapshot:
    """Capture a node/group/folder's name, enabled state, and parent before a test mutates it."""
    await _force_refresh_nodes(nucore_interface)
    node = (
        nucore_interface.nodes.get(raw_address)
        or nucore_interface.groups.get(raw_address)
        or nucore_interface.folders.get(raw_address)
    )
    if node is None:
        return NodeSnapshot(raw_address=raw_address)
    return NodeSnapshot(raw_address=raw_address, name=node.name, enabled=node.enabled, parent=node.parent)


async def restore_node(nucore_interface: Any, snapshot: NodeSnapshot) -> list[Any]:
    """Best-effort restore of a node's name/enabled/parent to its pre-test values."""
    if snapshot.name is None:
        return []

    results: list[Any] = []
    await _force_refresh_nodes(nucore_interface)
    current = (
        nucore_interface.nodes.get(snapshot.raw_address)
        or nucore_interface.groups.get(snapshot.raw_address)
        or nucore_interface.folders.get(snapshot.raw_address)
    )
    if current is None:
        return results

    if current.name != snapshot.name:
        results.append(await nucore_interface.node_ops(node_id=snapshot.raw_address, operation="rename", new_name=snapshot.name))
    if current.parent != snapshot.parent and snapshot.parent:
        results.append(await nucore_interface.node_ops(node_id=snapshot.raw_address, operation="move", new_parent_id=snapshot.parent))
    if current.enabled != snapshot.enabled and snapshot.enabled is not None:
        results.append(
            await nucore_interface.node_ops(node_id=snapshot.raw_address, operation="enable" if snapshot.enabled else "disable")
        )
    return results


async def delete_created_node(nucore_interface: Any, raw_address: str) -> Any:
    """Delete a group/folder a node_ops test case created (add_group/add_folder cases)."""
    return await nucore_interface.node_ops(node_id=raw_address, operation="delete")


async def list_node_ids(nucore_interface: Any) -> set[str]:
    """Return the current set of node/group/folder addresses, refreshing the structure first.

    Used the same way as ``list_routine_ids``: diff before/after to find the
    address a node_ops "add_group"/"add_folder" test case just created.
    """
    await _force_refresh_nodes(nucore_interface)
    return set(nucore_interface.nodes) | set(nucore_interface.groups) | set(nucore_interface.folders)


async def list_routine_ids(nucore_interface: Any) -> set[str]:
    """Return the current set of routine ids, refreshing the in-memory cache first.

    Used to detect which routine a creation-mode ``routine_content`` test case
    just created, by diffing the id set before/after the call -- robust
    regardless of exactly what shape the backend's create-routine HTTP
    response takes.
    """
    await _force_refresh_routines(nucore_interface)
    return {str(c["id"]) for c in nucore_interface.condensed_routines if c.get("id")}


async def snapshot_routine(nucore_interface: Any, routine_id: str | None) -> RoutineSnapshot:
    """Capture an existing routine's full definition, or record that none exists yet (creation case)."""
    if not routine_id:
        return RoutineSnapshot(routine_id=None)
    definition = await nucore_interface.get_routine(routine_id)
    return RoutineSnapshot(routine_id=routine_id, definition=definition)


async def restore_routine(nucore_interface: Any, snapshot: RoutineSnapshot, created_routine_id: str | None) -> Any:
    """Restore an edited routine to its pre-test definition, or delete one the test created.

    ``IoXWrapper.routine_ops``/``.delete_routine`` support a "delete" that the
    LLM-facing tool schema intentionally never exposes (routines shouldn't be
    autonomously deleted by the model) -- the harness calls it directly since
    it isn't going through the LLM/tool-call path.
    """
    if snapshot.routine_id and snapshot.definition:
        # Editing case: put the original definition back.
        return await nucore_interface.update_routine(snapshot.definition)
    if created_routine_id:
        # Creation case: remove the routine this test run created.
        return await nucore_interface.delete_routine(created_routine_id)
    return None

"""``create_or_update_routine`` -- authors/edits a routine's if/then/else
logic via the v2 DSL, compiled by ``unified.routine_compiler`` (a fresh
package targeting the new, externally-supplied ``Trigger``/``NewTrigger``
schema -- see that package's own ``__init__.py`` docstring for why it's a
brand-new package rather than a modification of the old, still-untouched
``intent_handler_directory/routine_automation/routine_compiler.py``, which
stays on the classic pipeline's old wire shape). ``get_device_detail`` --
the detail-fetch tool that closes the gap below.

v1 gap this closes: the DSL needs real property/command/parameter ids and
uom/precision (see tool_create_or_update_routine.json's description) --
fidelity the compact DEVICE DATABASE deliberately does not carry (design/
design.md decision #5), to keep prompt cost down. Rather than have the
backend silently resolve this (the send_command/get_property pattern) or
have the model guess, ``get_device_detail`` gives the model a way to fetch
one device's FULL specification as an explicit tool call, in the same
agentic turn, before it authors DSL code referencing that device -- the
compiler itself stays pure/stateless; resolution happens upstream, by the
model, not inside compilation.

``get_device_detail`` reuses ``ProfileRagFormatter.format_device_python``
(``src/rag/profile_rag_formatter.py``) -- the same renderer that already
backs the *classic* (non-unified) path's full-fidelity device context
(``NuCoreInterface.rags``, as opposed to the compact ``summary_rags`` the
unified DEVICE DATABASE uses) -- rather than writing new formatting logic.

``get_routine_detail`` closes the equivalent gap on the routine-content
side: ROUTINES DATABASE (``condensed_routines``) only carries
id/name/comment/device_names, never the actual if/then/else logic --
fetching that needs its own explicit tool call, via
``NuCoreInterface.get_routine`` (``GET /api/triggers/:id``), same as
``get_device_detail`` does for a device's full spec. Belongs here, not in
``routine_status_ops.py``, per this codebase's content-vs-runtime-state
split (``routine_status_ops`` is runtime state only -- enable/disable/run).

``get_routine_detail`` also annotates every enumeration-uom value (uom
25/146/148, per ``is_enumeration_uom``) with its real ``label`` --
deterministically, server-side, from the referenced device/command/
property's live ``Editor.ranges[*].names`` -- rather than returning a bare
raw index and relying on the model to remember it needs a follow-up
``get_device_detail`` call to translate it (it didn't, in practice: a real
query asked to explain a routine and the model reported raw index numbers
as if they were meaningful values). Same "backend does deterministic
lookup, never rely on model discipline for an exact fact" pattern as
``resolve_value``/``numeric_enum.py`` elsewhere in this codebase.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nucore import NuCoreInterface
from nucore.uom import is_enumeration_uom
from rag.profile_rag_formatter import ProfileRagFormatter
from unified.routine_compiler import TriggerCompileError, compile_trigger_source


def _op_ok(result: Any) -> bool:
    """``create_automation_routine``/``update_routine`` return a
    ``requests.Response`` on success, or ``None``/``False`` on failure --
    never raise on a hub-side rejection. A non-2xx response is still
    "not None"/truthy, so status_code must be checked explicitly (same
    class of bug already fixed in node_ops.py)."""
    if result is None or result is False or isinstance(result, str):
        return False
    status_code = getattr(result, "status_code", None)
    return status_code is not None and 200 <= status_code < 300


def _extract_hub_error_message(result: Any) -> str | None:
    """Pull the hub's own AI-friendly explanation out of a rejected
    create/update response body, e.g.
    ``{"successful": false, "errorCode": "BadRequestError", "errorMessage": "Invalid program"}``
    -- surfaced to the model so a repair turn can act on *why* the hub
    rejected the compiled trigger, not just its bare HTTP status code."""
    json_method = getattr(result, "json", None)
    if not callable(json_method):
        return None
    try:
        body = json_method()
    except Exception:
        return None
    if not isinstance(body, dict):
        return None
    message = body.get("errorMessage")
    code = body.get("errorCode")
    if message and code:
        return f"{code}: {message}"
    return message or code


def _op_error(result: Any) -> str:
    if result is None or result is False:
        return "no response from backend"
    if isinstance(result, str):
        return result
    status_code = getattr(result, "status_code", None)
    hub_message = _extract_hub_error_message(result)
    if hub_message:
        return f"HTTP {status_code}: {hub_message}" if status_code is not None else hub_message
    return f"HTTP {status_code}" if status_code is not None else str(result)


async def _find_routine_id_by_name(nucore_interface: NuCoreInterface, name: str) -> int | None:
    """Look up a routine's real id by name after a forced refresh. Never
    trust an id echoed back from a create call -- the create response isn't
    guaranteed to carry the hub-assigned id, so this is the only path,
    not a fallback. Searches most-recently-loaded entries first in case a
    stale duplicate from an earlier refresh shares the same name."""
    nucore_interface.routines_changed = True
    await nucore_interface._refresh_routines_database()
    for routine in reversed(nucore_interface.condensed_routines):
        if routine.get("name") == name:
            return routine.get("id")
    return None


async def get_device_detail(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    device_id = args.get("device_id")
    if not device_id:
        return {"error": "device_id is required"}

    node = nucore_interface.get_node(device_id)
    if node is None:
        return {"error": f"no device/group found with id '{device_id}'; check DEVICE DATABASE"}
    if getattr(node, "node_def", None) is None:
        return {"error": f"'{device_id}' has no properties/commands (likely a folder, not a device/group)"}

    # format_device_python only reads self.nodes/groups/folders (for parent
    # lookups) -- no need to run the full formatter pipeline for one device.
    formatter = ProfileRagFormatter(json_output=True)
    formatter.nodes = nucore_interface.nodes
    formatter.groups = nucore_interface.groups
    formatter.folders = nucore_interface.folders

    return {"device_id": device_id, "detail": formatter.format_device_python(node)}


def _find_property_editor(node_def: Any, property_id: Any):
    prop = node_def.properties.get(property_id)
    return prop.editor if prop else None


def _find_command_param_editor(node_def: Any, command_id: Any, param_id: Any, direction: str):
    commands = node_def.cmds.accepts if direction == "accepts" else node_def.cmds.sends
    for cmd in commands:
        if cmd.id == command_id:
            for param in cmd.parameters:
                if param.id == param_id:
                    return param.editor
    return None


def _label_for(editor: Any, value: Any) -> str | None:
    if editor is None or not editor.ranges:
        return None
    for r in editor.ranges:
        names = getattr(r, "names", None)
        if names:
            label = names.get(str(value))
            if label:
                return label
    return None


def _annotate_val(val: Any, editor: Any) -> None:
    if not isinstance(val, dict):
        return
    uom, value = val.get("uom"), val.get("value")
    if uom is None or value is None or not is_enumeration_uom(uom):
        return
    label = _label_for(editor, value)
    if label:
        val["label"] = label


def _annotate_condition(nucore_interface: NuCoreInterface, condition: Any) -> None:
    if not isinstance(condition, dict):
        return
    ctype = condition.get("type")
    if ctype == "paren":
        for nested in condition.get("conditions") or []:
            _annotate_condition(nucore_interface, nested)
        return
    if ctype != "status":
        return  # control/var/x10/inet/triggerref/schedule/comment carry no enum-uom val to translate
    node = nucore_interface.get_node(condition.get("node"))
    node_def = getattr(node, "node_def", None)
    if node_def is None:
        return
    editor = _find_property_editor(node_def, condition.get("id"))
    _annotate_val(condition.get("val"), editor)


def _annotate_action(nucore_interface: NuCoreInterface, action: Any) -> None:
    if not isinstance(action, dict) or action.get("type") != "cmd":
        return  # only `cmd` actions carry param values with a uom to translate
    node = nucore_interface.get_node(action.get("node"))
    node_def = getattr(node, "node_def", None)
    if node_def is None:
        return
    for param in action.get("p") or []:
        if not isinstance(param, dict) or param.get("type") != "val":
            continue
        editor = _find_command_param_editor(node_def, action.get("id"), param.get("id"), "accepts")
        _annotate_val(param.get("val"), editor)


async def get_routine_detail(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    routine_id = args.get("id")
    if not routine_id:
        return {"error": "id is required"}

    try:
        result = await nucore_interface.get_routine(routine_id)
    except Exception as exc:
        return {"error": f"failed to fetch routine detail: {exc}"}

    if not isinstance(result, dict):
        return {"error": f"failed to fetch routine '{routine_id}': {_op_error(result)}"}

    for condition in result.get("if") or []:
        _annotate_condition(nucore_interface, condition)
    for action in (result.get("then") or []) + (result.get("else") or []):
        _annotate_action(nucore_interface, action)

    return result


async def create_or_update_routine(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    name = args.get("name")
    routine_id = args.get("id")
    comment = args.get("comment")
    code = args.get("code")
    if not name or not code:
        return {"error": "name and code are both required"}

    parent = None
    if routine_id:
        # Confirmed: an update must carry the routine's existing `parent`,
        # or the update fails/misbehaves. `parent` is placement metadata,
        # never expressed in the DSL -- fetched here directly rather than
        # trusting the model to have read and correctly threaded it through
        # from an earlier get_routine_detail call (same "backend does the
        # exact lookup, never rely on model discipline for a fact" pattern
        # used throughout this codebase).
        try:
            existing = await nucore_interface.get_routine(routine_id)
        except Exception as exc:
            return {"error": f"failed to fetch existing routine '{routine_id}' before update: {exc}"}
        if not isinstance(existing, dict):
            return {"error": f"failed to fetch existing routine '{routine_id}' before update: {_op_error(existing)}"}
        parent = existing.get("parent")

    try:
        compiled = compile_trigger_source(
            name=name, trigger_id=routine_id, comment=comment, source=code, parent=parent
        )
    except TriggerCompileError as exc:
        return {"error": str(exc)}

    try:
        if routine_id:
            result = await nucore_interface.update_routine(compiled)
        else:
            result = await nucore_interface.create_automation_routine(compiled)
    except Exception as exc:
        return {"error": f"failed to save routine: {exc}"}

    if not _op_ok(result):
        return {"error": f"failed to save routine '{name}': {_op_error(result)}"}

    if not routine_id:
        await asyncio.sleep(2)
        routine_id = await _find_routine_id_by_name(nucore_interface, name)
        if routine_id is None:
            return {
                "name": name,
                "status": "saved",
                "warning": "routine was created but its new id could not be found afterward",
            }

    return {"name": name, "id": routine_id, "status": "saved"}

"""``delete_folder`` -- deletes a routine folder (see routine_automation.py's
``is_folder``/``folder_id``/``move_to_folder_id``). Distinct from every other
delete-capable tool in this codebase because a non-empty folder needs a
customer decision about its CONTENTS (delete them too, or move them
elsewhere first) on top of the usual two-call ``confirmed`` gate -- so this
module layers that decision step in front of the same confirm shape
``node_ops.py``/``variable_ops.py``/``routine_status_ops.py`` already use.
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface


def _op_ok(result: Any) -> bool:
    """Same non-raising-REST-call success check every other handler module
    in this codebase duplicates (node_ops.py/variable_ops.py/
    routine_status_ops.py/routine_automation.py) -- a plain error string or
    ``None``/``False`` is not success, regardless of status code presence."""
    if result is None or result is False or isinstance(result, str):
        return False
    status_code = getattr(result, "status_code", None)
    return status_code is not None and 200 <= status_code < 300


def _op_error(result: Any) -> str:
    if result is None or result is False:
        return "no response from backend"
    if isinstance(result, str):
        return result
    status_code = getattr(result, "status_code", None)
    return f"HTTP {status_code}" if status_code is not None else str(result)


_YES_GATE_NOTE = (
    "The system only honors this if their very next reply is exactly the word YES (all caps) and "
    "nothing else; it independently verifies this regardless of what this call's own confirmed "
    "argument says."
)


async def _children_of(nucore_interface: NuCoreInterface, folder_id: int) -> list[dict[str, Any]]:
    """Every routine/folder whose ``parent`` is *folder_id*, from a forced
    fresh read -- ``routines_changed`` is set before refreshing so a sibling
    call earlier in this same turn (which only flips that flag, see
    variable_ops.py's own comment on this) is reflected here, not just on
    the next turn's prompt build."""
    nucore_interface.routines_changed = True
    await nucore_interface._refresh_routines_database()
    return [routine for routine in nucore_interface.all_routines.values() if routine.get("parent") == folder_id]


async def _delete_folder_itself(
    nucore_interface: NuCoreInterface, folder_id: int, *, contents_action: str | None, affected_ids: list[Any]
) -> Any:
    try:
        result = await nucore_interface.routine_ops(folder_id, "delete")
    except Exception as exc:
        return {"error": f"failed to delete folder {folder_id}: {exc}"}
    if not _op_ok(result):
        return {"error": f"failed to delete folder {folder_id}: {_op_error(result)}"}

    nucore_interface.routines_changed = True
    response: dict[str, Any] = {"folder_id": folder_id, "status": "ok"}
    if contents_action is not None:
        response["contents_action"] = contents_action
        response["affected_ids"] = affected_ids
    return response


async def delete_folder(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    folder_id = args.get("folder_id")
    if folder_id is None:
        return {"error": "folder_id is required"}

    try:
        children = await _children_of(nucore_interface, folder_id)
    except Exception as exc:
        return {"error": f"failed to enumerate folder {folder_id}'s contents: {exc}"}

    if not children:
        if not bool(args.get("confirmed")):
            return {
                "confirmation_required": True,
                "folder_id": folder_id,
                "message": (
                    "This permanently deletes this empty folder -- nothing has been deleted yet. "
                    "Call delete_folder again with confirmed: true only after the customer has "
                    f"explicitly agreed, never speculatively. {_YES_GATE_NOTE}"
                ),
            }
        return await _delete_folder_itself(nucore_interface, folder_id, contents_action=None, affected_ids=[])

    contents_action = args.get("contents_action")
    if contents_action not in ("delete", "move"):
        return {
            "confirmation_required": "choose_contents_action",
            "folder_id": folder_id,
            "children": [{"id": c.get("id"), "name": c.get("name")} for c in children],
            "message": (
                f"This folder contains {len(children)} item(s). Ask the customer: (a) delete them "
                "all along with the folder, or (b) move them into a different folder first (get its "
                "name/id). Call delete_folder again with contents_action set to 'delete' or 'move' "
                "(plus move_to_folder_id for 'move'), and confirmed: true, only after the customer "
                "has explicitly agreed."
            ),
        }

    move_to_folder_id = args.get("move_to_folder_id")
    if contents_action == "move":
        if move_to_folder_id is None:
            return {"error": "move_to_folder_id is required when contents_action is 'move'"}
        if move_to_folder_id == folder_id:
            return {"error": "move_to_folder_id must be a different folder than folder_id"}

    if not bool(args.get("confirmed")):
        what = (
            f"this folder and all {len(children)} item(s) inside it"
            if contents_action == "delete"
            else f"this folder, after moving its {len(children)} item(s) into folder {move_to_folder_id}"
        )
        return {
            "confirmation_required": True,
            "folder_id": folder_id,
            "contents_action": contents_action,
            "message": (
                f"This permanently deletes {what} -- nothing has been deleted or moved yet. Call "
                "delete_folder again with confirmed: true only after the customer has explicitly "
                f"agreed, never speculatively. {_YES_GATE_NOTE}"
            ),
        }

    # Refuse to cascade into a nested folder that itself has contents --
    # nested-folder enumeration/behavior hasn't been verified against a live
    # hub yet (see design/iox_apis/routines_folders_mgmt.md's open risks).
    # Safety-first default: surface it distinctly rather than silently
    # mis-handling it.
    all_routines = nucore_interface.all_routines
    blocked = [
        c for c in children
        if c.get("isFolder") and any(r.get("parent") == c.get("id") for r in all_routines.values())
    ]
    if blocked:
        names = ", ".join(f"{c.get('name')} (id {c.get('id')})" for c in blocked)
        return {
            "error": (
                f"This folder contains a non-empty nested folder that delete_folder refuses to "
                f"cascade into automatically: {names}. Resolve it directly first (delete or move "
                "its own contents), then retry."
            )
        }

    child_ids = [c.get("id") for c in children]
    failed: list[tuple[Any, str]] = []

    if contents_action == "delete":
        for child_id in child_ids:
            try:
                result = await nucore_interface.routine_ops(child_id, "delete")
            except Exception as exc:
                failed.append((child_id, str(exc)))
                continue
            if not _op_ok(result):
                failed.append((child_id, _op_error(result)))
    else:  # move
        for child_id in child_ids:
            try:
                full = await nucore_interface.get_routine(child_id)
            except Exception as exc:
                failed.append((child_id, str(exc)))
                continue
            if not isinstance(full, dict):
                failed.append((child_id, _op_error(full)))
                continue
            try:
                result = await nucore_interface.update_routine({**full, "parent": move_to_folder_id})
            except Exception as exc:
                failed.append((child_id, str(exc)))
                continue
            if not _op_ok(result):
                failed.append((child_id, _op_error(result)))

    if failed:
        # Whatever children DID succeed before the failing one really did
        # mutate on the hub -- _children_of's own flag-set above was already
        # consumed by its own forced refresh, and _delete_folder_itself
        # (which would set it again) is never reached on this early return,
        # so without this, those successful mutations would be invisible to
        # condensed_routines/ROUTINES DATABASE until something else (the
        # hub's own `_1` event) happens to flip the flag.
        nucore_interface.routines_changed = True
        detail = "; ".join(f"id {cid}: {err}" for cid, err in failed)
        return {
            "error": (
                f"failed to {contents_action} folder {folder_id}'s contents, folder itself left in "
                f"place: {detail}"
            )
        }

    return await _delete_folder_itself(nucore_interface, folder_id, contents_action=contents_action, affected_ids=child_ids)

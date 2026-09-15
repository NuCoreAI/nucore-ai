"""``get_device_history`` -- structured or raw-SQL query against DEVLOG.DB,
the ISY/eisy firmware's own structured device-activity capture. Device/
property resolution, the real sqlite3 CLI call, SQL-safety validation, and
row-cap/truncation all live in IoXWrapper.get_device_history (see
tool_device_get_history.json's description for the full schema/semantics
carried to the model); this handler stays thin -- arg validation, mode
dispatch, unwrapping the {"successful", "data"} envelope, and computing
the structured-mode pagination hint fields (mirrors run_shell_command's
truncated/timed_out signal pattern rather than inventing a new one).
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface


def _op_error(result: Any) -> str:
    if isinstance(result, dict):
        return str(result.get("data") or "unknown error")
    if result is None:
        return "no response from backend"
    return str(result)


async def get_device_history(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    sql = args.get("sql")
    device_ids = args.get("device_ids")
    properties = args.get("properties")

    if sql:
        if device_ids or properties or args.get("start") or args.get("end"):
            return {"error": "pass either sql, or device_ids/properties/start/end -- not both"}
        try:
            result = await nucore_interface.get_device_history(sql=sql, limit=args.get("limit") or 500)
        except Exception as exc:
            return {"error": f"failed to query device history: {exc}"}
        if not isinstance(result, dict) or not result.get("successful"):
            return {"error": _op_error(result)}
        return {"rows": result.get("data"), "truncated": bool(result.get("truncated"))}

    if not isinstance(device_ids, list) or not device_ids:
        return {"error": "device_ids is required and must be a non-empty list (or pass sql instead)"}
    if not isinstance(properties, list) or not properties:
        return {"error": "properties is required and must be a non-empty list (or pass sql instead)"}

    limit = args.get("limit") or 500

    try:
        result = await nucore_interface.get_device_history(
            device_ids,
            properties,
            start=args.get("start"),
            end=args.get("end"),
            one_before=bool(args.get("one_before", False)),
            one_after=bool(args.get("one_after", False)),
            limit=limit,
        )
    except Exception as exc:
        return {"error": f"failed to query device history: {exc}"}

    if not isinstance(result, dict) or not result.get("successful"):
        return {"error": _op_error(result)}

    groups = result.get("data")
    if not isinstance(groups, list):
        # IoXWrapper's implementation always normalizes to a list -- this is
        # a defensive floor for any other NuCoreInterface implementation,
        # not an expected path.
        return {"error": "device history query returned an unexpected shape", "raw": groups}

    total_records = sum(len(g.get("history") or []) for g in groups if isinstance(g, dict))
    response: dict[str, Any] = {"results": groups}
    if total_records >= limit:
        last_timestamps = [
            g["history"][-1].get("timestamp") for g in groups if isinstance(g, dict) and g.get("history")
        ]
        last_timestamps = [t for t in last_timestamps if t]
        response["more_available"] = True
        if last_timestamps:
            response["next_start"] = max(last_timestamps)
    return response

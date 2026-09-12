"""``set_node_property_history_recording``/``get_node_property_history`` --
the historical counterpart to ``get_property``/``send_command``
(``command_control_status.py``): turning node-property history recording
on/off, and querying recorded values over time. See ``design/history.md``
for the reverse-engineered backend contract these wrap, and
``design/history_impl.md`` for this feature's implementation plan.

Device/property resolution, the actual HTTP call, and parsing the backend's
XML response into a normalized list of ``{"node", "property",
"property_name", "history"}`` groups all live in
``IoXWrapper.get_node_property_history`` (confirmed against a live hub --
see ``_parse_node_property_history_xml`` in ``iox_wrapper.py``); this
handler stays thin -- arg validation, unwrapping the
``{"successful", "data"}`` envelope into a clean tool result, and computing
the pagination hint fields (mirrors ``run_shell_command``'s
``truncated``/``timed_out`` signal pattern rather than inventing a new one).
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


async def set_node_property_history_recording(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    enabled = args.get("enabled")
    if not isinstance(enabled, bool):
        return {"error": "enabled is required and must be true or false"}

    try:
        result = await nucore_interface.set_node_property_history_recording(enabled)
    except Exception as exc:
        return {"error": f"failed to set node property history recording: {exc}"}

    if not isinstance(result, dict) or not result.get("successful"):
        return {"error": _op_error(result)}

    return {"node_property_history_recording": "on" if enabled else "off"}


async def get_node_property_history(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    device_ids = args.get("device_ids")
    properties = args.get("properties")
    if not isinstance(device_ids, list) or not device_ids:
        return {"error": "device_ids is required and must be a non-empty list"}
    if not isinstance(properties, list) or not properties:
        return {"error": "properties is required and must be a non-empty list"}

    limit = args.get("limit") or 500

    try:
        result = await nucore_interface.get_node_property_history(
            device_ids,
            properties,
            start=args.get("start"),
            end=args.get("end"),
            one_before=bool(args.get("one_before", False)),
            one_after=bool(args.get("one_after", False)),
            limit=limit,
        )
    except Exception as exc:
        return {"error": f"failed to fetch node property history: {exc}"}

    if not isinstance(result, dict) or not result.get("successful"):
        return {"error": _op_error(result)}

    groups = result.get("data")
    if not isinstance(groups, list):
        # IoXWrapper's implementation always normalizes to a list -- this is
        # a defensive floor for any other NuCoreInterface implementation,
        # not an expected path.
        return {"error": "history endpoint returned an unexpected shape", "raw": groups}

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

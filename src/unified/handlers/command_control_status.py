"""``get_property``/``send_command`` -- the only two of the five baseline
capability areas that need the Phase 1 name/value resolution backend work.

Dispatch calling directly into ``NuCoreInterface``/``IoXWrapper``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from nucore import NuCoreInterface
from nucore.numeric_enum import NUMERIC_ENUM_EDITOR_IDS, resolve_numeric_enum
from nucore.value_resolution import ValueResolutionError, resolve_value


async def get_property(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    entries = args.get("properties")
    if not isinstance(entries, list) or not entries:
        return {"error": "'properties' must be a non-empty list"}

    # entries_by_device[device_id] -> list of (original index, property name)
    # -- fetching get_properties(device_id) once per unique device regardless
    # of how many entries target it is the whole point of this rewrite: that
    # call already returns every property for the device in one hub round
    # trip, so re-fetching per entry would throw away the free batching win.
    entries_by_device: dict[str, list[tuple[int, str]]] = {}
    results: list[dict[str, Any] | None] = [None] * len(entries)

    for idx, entry in enumerate(entries):
        if not isinstance(entry, dict):
            results[idx] = {"successful": False, "error": f"entry at index {idx} is not an object"}
            continue
        device_id = entry.get("device_id")
        property_name = entry.get("property")
        if not device_id or not property_name:
            results[idx] = {
                "device_id": device_id, "successful": False,
                "error": "device_id and property are both required",
            }
            continue
        entries_by_device.setdefault(device_id, []).append((idx, property_name))

    async def _fetch_one_device(device_id: str, wanted: list[tuple[int, str]]) -> None:
        node = nucore_interface.get_node(device_id)
        if node is None:
            for idx, property_name in wanted:
                results[idx] = {
                    "device_id": device_id, "property": property_name, "successful": False,
                    "error": f"no device found with id '{device_id}'; check DEVICE DATABASE",
                }
            return

        properties = await nucore_interface.get_properties(device_id)

        for idx, property_name in wanted:
            if property_name == "*":
                if not properties:
                    results[idx] = {
                        "device_id": device_id, "device": node.name, "successful": False,
                        "error": "could not read properties for this device",
                    }
                    continue
                # A wildcard entry expands into one result per property
                # actually present -- collected as a list under this one
                # index, flattened into `results` below.
                expanded = []
                node_def_props = getattr(node.node_def, "properties", {}) if node.node_def else {}
                for property_id, prop in properties.items():
                    def_prop = node_def_props.get(property_id)
                    expanded.append({
                        "device_id": device_id, "device": node.name,
                        "property": def_prop.name if def_prop and def_prop.name else property_id,
                        "value": prop.formatted or prop.value, "successful": True,
                    })
                results[idx] = expanded
                continue

            property_id = nucore_interface.resolve_property_id(device_id, property_name)
            if property_id is None:
                results[idx] = {
                    "device_id": device_id, "device": node.name, "property": property_name,
                    "successful": False,
                    "error": f"'{property_name}' is not a known property for this device; please clarify",
                }
                continue
            prop = properties.get(property_id) if properties else None
            if prop is None:
                results[idx] = {
                    "device_id": device_id, "device": node.name, "property": property_name,
                    "successful": False,
                    "error": f"could not read a current value for '{property_name}' on this device",
                }
                continue
            results[idx] = {
                "device_id": device_id, "device": node.name, "property": property_name,
                "value": prop.formatted or prop.value, "successful": True,
            }

    await asyncio.gather(*(_fetch_one_device(d, w) for d, w in entries_by_device.items()))

    flat_results: list[dict[str, Any]] = []
    for r in results:
        if isinstance(r, list):
            flat_results.extend(r)
        elif r is not None:
            flat_results.append(r)

    successful = sum(1 for r in flat_results if r["successful"])
    failed = len(flat_results) - successful
    response: dict[str, Any] = {
        "summary": {"total": len(flat_results), "successful": successful, "failed": failed},
        "results": flat_results,
    }
    if failed:
        failed_desc = ", ".join(
            f"{r.get('device') or r.get('device_id')}/{r.get('property', '?')}"
            for r in flat_results if not r["successful"]
        )
        response["error"] = f"{failed} of {len(flat_results)} could not be read: {failed_desc} -- see 'results' for per-entry detail"
    return response


def _resolve_command_parameters(
    command: Any, entry: dict[str, Any], command_name: str
) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Resolve one ``commands[]`` entry's value(s) against *command*'s declared
    parameters. Returns ``(parameters, None)`` on success, or ``(None, error)``
    on failure -- never raises, so one bad entry in a multi-command call can't
    take down its siblings' already-resolved results."""
    try:
        parameters: list[dict[str, Any]] = []
        if not command.parameters:
            return parameters, None

        values = entry.get("values")
        if values is None:
            # Single-value shape -- also covers the common one-parameter case
            # without requiring the model to use `values`.
            values = [{"value": entry.get("value"), "unit": entry.get("unit")}]
        if len(values) < len(command.parameters):
            # Pad missing trailing entries with no value -- resolved below as
            # "omitted", which is only valid if that parameter is optional.
            values = list(values) + [{"value": None}] * (len(command.parameters) - len(values))
        elif len(values) > len(command.parameters):
            return None, (
                f"'{command_name}' takes {len(command.parameters)} parameter(s), "
                f"got {len(values)}; please clarify"
            )

        for param, value_entry in zip(command.parameters, values):
            value = value_entry.get("value") if isinstance(value_entry, dict) else value_entry
            unit = value_entry.get("unit") if isinstance(value_entry, dict) else None
            param_label = param.name or param.id or "value"

            if value is None:
                if not param.optional:
                    return None, f"'{command_name}' requires a value for '{param_label}'; please clarify"
                # Genuinely optional in the source profile data (CommandParameter.optional,
                # populated from profile.py) -- omit rather than forcing the model to
                # invent a value the device doesn't require.
                continue
            elif param.editor is not None and param.editor.id in NUMERIC_ENUM_EDITOR_IDS:
                # Disguised-numeric editor (e.g. keypad backlight, ramp rate) --
                # value/uom/precision aren't meaningful here, only the resolved
                # raw index (see nucore.numeric_enum for why this needs its own
                # path instead of resolve_value's enum-label/precision model).
                try:
                    index = resolve_numeric_enum(param.editor, value, unit=unit)
                except ValueError as exc:
                    return None, str(exc)
                parameters.append(
                    {
                        "id": param.id if param.id else "n/a",
                        "value": index,
                        "uom": int(param.editor.ranges[0].uom.id) if param.editor.ranges[0].uom else 0,
                        "precision": 0,
                    }
                )
            elif param.editor is None or not param.editor.ranges:
                # Free-text parameter (e.g. a notification message body) -- no
                # enum/numeric editor to validate against, pass the value through raw.
                parameters.append(
                    {
                        "id": param.id if param.id else "n/a",
                        "value": value,
                        "uom": 0,
                        "precision": 0,
                    }
                )
            else:
                try:
                    resolved = resolve_value(param.editor, value=value, unit=unit)
                except ValueResolutionError as exc:
                    return None, str(exc)
                parameters.append(
                    {
                        "id": param.id if param.id else "n/a",
                        "value": resolved.value,
                        "uom": resolved.uom,
                        "precision": resolved.precision,
                    }
                )
        return parameters, None
    except Exception as exc:
        return None, f"failed to resolve '{command_name}': {exc}"


async def send_command(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    commands = args.get("commands")
    if not isinstance(commands, list) or not commands:
        return {"error": "'commands' must be a non-empty list"}

    results: list[dict[str, Any]] = []
    to_send: list[dict[str, Any]] = []
    # to_send[i] resolves to results[send_index_map[i]] -- entries that fail
    # resolution never reach `to_send`, so this mapping can't be a plain range().
    send_index_map: list[int] = []

    for idx, entry in enumerate(commands):
        if not isinstance(entry, dict):
            results.append(
                {"index": idx, "device": None, "command": None, "successful": False,
                 "error": f"command entry at index {idx} is not an object"}
            )
            continue

        device_id = entry.get("device_id")
        command_name = entry.get("command")
        if not device_id or not command_name:
            results.append(
                {"index": idx, "device": device_id, "command": command_name, "successful": False,
                 "error": "device_id and command are both required"}
            )
            continue

        node = nucore_interface.get_node(device_id)
        if node is None:
            results.append(
                {"index": idx, "device": device_id, "command": command_name, "successful": False,
                 "error": f"no device found with id '{device_id}'; check DEVICE DATABASE"}
            )
            continue

        command = nucore_interface.resolve_command_id(device_id, command_name, direction="accepts")
        if command is None:
            results.append(
                {"index": idx, "device": node.name, "command": command_name, "successful": False,
                 "error": f"'{command_name}' is not a known command for this device; please clarify"}
            )
            continue

        parameters, param_error = _resolve_command_parameters(command, entry, command_name)
        if param_error:
            results.append(
                {"index": idx, "device": node.name, "command": command_name, "successful": False,
                 "error": param_error}
            )
            continue

        # Filled in below once send_commands() returns.
        results.append({"index": idx, "device": node.name, "command": command_name, "successful": False})
        send_index_map.append(len(results) - 1)
        # device_id (not node.address) -- send_commands does its own decode_id
        # step, expecting the id exactly as the caller/model supplied it.
        to_send.append({"device": device_id, "command": command.id, "parameters": parameters})

    if to_send:
        try:
            responses = await nucore_interface.send_commands(to_send)
        except Exception as exc:
            for result_pos in send_index_map:
                results[result_pos]["error"] = f"failed to send command: {exc}"
        else:
            # Correlation by position relies on send_commands() never
            # reordering or dropping well-formed entries -- true today
            # (IoXWrapper._send_commands is a plain sequential for-loop), but
            # would break silently if that ever became concurrent.
            responses = responses or []
            for send_pos, result_pos in enumerate(send_index_map):
                response = responses[send_pos] if send_pos < len(responses) else None
                ok = getattr(response, "status_code", None) == 200
                results[result_pos]["successful"] = ok
                if not ok:
                    results[result_pos]["error"] = (
                        "no response received from the hub for this command" if response is None
                        else f"hub rejected command (status {getattr(response, 'status_code', None)})"
                    )

    successful = sum(1 for r in results if r["successful"])
    failed = len(results) - successful
    response: dict[str, Any] = {
        "summary": {"total": len(results), "successful": successful, "failed": failed},
        "results": results,
    }
    if failed:
        # A top-level "error" key is this codebase's one universal signal for
        # "this call did not fully succeed" -- the model's mandatory-tool-use
        # self-check keys off its mere presence, not off summary/results,
        # which are easy to miss.
        failed_desc = ", ".join(f"{r['device']}/{r['command']}" for r in results if not r["successful"])
        response["error"] = f"{failed} of {len(results)} command(s) failed: {failed_desc} -- see 'results' for per-entry detail"
    return response

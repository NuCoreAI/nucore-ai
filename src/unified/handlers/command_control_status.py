"""``get_property``/``send_command`` -- the only two of the five baseline
capability areas that need the Phase 1 name/value resolution backend work.

Fresh dispatch calling directly into ``NuCoreInterface``/``IoXWrapper`` --
does not import or invoke ``intent_handler_directory/command_control_status``.
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface
from nucore.numeric_enum import NUMERIC_ENUM_EDITOR_IDS, resolve_numeric_enum
from nucore.value_resolution import ValueResolutionError, resolve_value


async def get_property(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    device_id = args.get("device_id")
    property_name = args.get("property")
    if not device_id or not property_name:
        return {"error": "device_id and property are both required"}

    node = nucore_interface.get_node(device_id)
    if node is None:
        return {"error": f"no device found with id '{device_id}'; check DEVICE DATABASE"}

    property_id = nucore_interface.resolve_property_id(device_id, property_name)
    if property_id is None:
        return {"error": f"'{property_name}' is not a known property for this device; please clarify"}

    properties = await nucore_interface.get_properties(device_id)
    prop = properties.get(property_id) if properties else None
    if prop is None:
        return {"error": f"could not read a current value for '{property_name}' on this device"}

    return {
        "device": node.name,
        "property": property_name,
        "value": prop.formatted or prop.value,
    }


async def send_command(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    device_id = args.get("device_id")
    command_name = args.get("command")
    if not device_id or not command_name:
        return {"error": "device_id and command are both required"}

    node = nucore_interface.get_node(device_id)
    if node is None:
        return {"error": f"no device found with id '{device_id}'; check DEVICE DATABASE"}

    command = nucore_interface.resolve_command_id(device_id, command_name, direction="accepts")
    if command is None:
        return {"error": f"'{command_name}' is not a known command for this device; please clarify"}

    parameters: list[dict[str, Any]] = []
    if command.parameters:
        if len(command.parameters) > 1:
            return {
                "error": (
                    f"'{command_name}' takes multiple parameters, which this tool doesn't support yet; "
                    "please clarify or try a simpler command"
                )
            }
        param = command.parameters[0]
        value = args.get("value")
        if value is None:
            if not param.optional:
                return {"error": f"'{command_name}' requires a value; please clarify"}
            # Genuinely optional in the source profile data (CommandParameter.optional,
            # populated from profile.py) -- send with no parameters rather than
            # forcing the model to invent a value the device doesn't require.
        elif param.editor is not None and param.editor.id in NUMERIC_ENUM_EDITOR_IDS:
            # Disguised-numeric editor (e.g. keypad backlight, ramp rate) --
            # value/uom/precision aren't meaningful here, only the resolved
            # raw index (see nucore.numeric_enum for why this needs its own
            # path instead of resolve_value's enum-label/precision model).
            try:
                index = resolve_numeric_enum(param.editor, value, unit=args.get("unit"))
            except ValueError as exc:
                return {"error": str(exc)}
            parameters.append(
                {
                    "id": param.id if param.id else "n/a",
                    "value": index,
                    "uom": int(param.editor.ranges[0].uom.id) if param.editor.ranges[0].uom else 0,
                    "precision": 0,
                }
            )
        else:
            try:
                resolved = resolve_value(param.editor, value=value, unit=args.get("unit"))
            except ValueResolutionError as exc:
                return {"error": str(exc)}
            parameters.append(
                {
                    "id": param.id if param.id else "n/a",
                    "value": resolved.value,
                    "uom": resolved.uom,
                    "precision": resolved.precision,
                }
            )

    # device_id (not node.address) -- send_commands does its own decode_id
    # step, expecting the id exactly as the caller/model supplied it.
    device_command = {"device": device_id, "command": command.id, "parameters": parameters}
    try:
        await nucore_interface.send_commands([device_command])
    except Exception as exc:
        return {"error": f"failed to send command: {exc}"}

    return {"device": node.name, "command": command_name, "status": "sent"}

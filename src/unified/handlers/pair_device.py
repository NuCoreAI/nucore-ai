"""``pair_device`` -- add/pair one physical device, as a standalone global
tool (not gated behind a Plan session -- see ``dispatch.py``'s
``_PLAN_EXEMPT_TOOLS``, which lets this run even while a plan session is in
progress).

Two structurally different pairing shapes, not one:
- ``add_by_address`` -- the customer already has/reads the device's own
  address (insteon/x10 only; z-wave/zigbee/matter devices have no usable
  address until physically activated during an inclusion window).
- ``start_inclusion``/``finish_inclusion`` -- put the controller in pairing
  mode, the customer activates one or more devices, then a separate call
  commits everything included during that window. The only shape
  z-wave/zigbee/matter will ever use.

Only insteon has a real backend today; every other protocol/action
combination that is nonetheless structurally valid (see
``_PROTOCOL_ACTIONS``) returns a "not yet supported" message rather than an
error, so the model falls back to walking the customer through manual
pairing.
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface

_PROTOCOL_ACTIONS: dict[str, frozenset[str]] = {
    "insteon": frozenset({"add_by_address", "start_inclusion", "finish_inclusion"}),
    "x10": frozenset({"add_by_address"}),
    "zwave": frozenset({"start_inclusion", "finish_inclusion"}),
    "zigbee": frozenset({"start_inclusion", "finish_inclusion"}),
    "matter": frozenset({"start_inclusion", "finish_inclusion"}),
}

# Everything else in _PROTOCOL_ACTIONS is a real, valid protocol/action
# shape that just isn't backed by a real call yet.
_IMPLEMENTED_PROTOCOLS = frozenset({"insteon"})


async def pair_device(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    protocol = args.get("protocol")
    valid_actions = _PROTOCOL_ACTIONS.get(protocol)
    if valid_actions is None:
        return {"error": f"unknown protocol '{protocol}' -- must be one of: {sorted(_PROTOCOL_ACTIONS)}"}

    action = args.get("action")
    if action not in valid_actions:
        return {"error": f"action '{action}' is not valid for protocol '{protocol}' -- must be one of: {sorted(valid_actions)}"}

    if protocol not in _IMPLEMENTED_PROTOCOLS:
        return (
            f"Pairing for '{protocol}' is not yet supported -- guide the customer "
            "through the vendor's manual pairing procedure instead."
        )

    # protocol == "insteon" from here on.
    if action == "add_by_address":
        device_address = args.get("device_address")
        if not device_address:
            return {"error": "device_address is required for add_by_address"}
        result = await nucore_interface.add_device(
            device_address, name=args.get("name"), device_type=args.get("device_type"),
        )
        if result is None:
            return {"error": f"failed to add device '{device_address}'"}
        return {"protocol": protocol, "action": action, "device_address": result, "status": "added"}

    if action == "start_inclusion":
        ok = await nucore_interface.discover_devices(device_type=args.get("device_type"))
        if not ok:
            return {"error": "failed to start inclusion mode"}
        return {
            "protocol": protocol, "action": action, "status": "inclusion_started",
            "note": "The hub is now in pairing mode -- tell the customer to activate each device "
                    "they want to add, then call finish_inclusion to commit.",
        }

    # action == "finish_inclusion"
    flag = args.get("flag", 1)
    ok = await nucore_interface.finish_device_discovery(flag=flag)
    if not ok:
        return {"error": "failed to commit inclusion session"}
    return {
        "protocol": protocol, "action": action, "status": "inclusion_committed",
        "note": "Newly included devices don't come back with an address-to-name mapping here -- "
                "refresh the device list to find them, then rename/assign as needed.",
    }

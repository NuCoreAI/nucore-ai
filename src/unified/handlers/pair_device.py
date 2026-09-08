"""``pair_device`` -- add/pair one physical device, as a standalone global
tool (not gated behind a Plan session -- see ``dispatch.py``'s
``_PLAN_ALWAYS_IMMEDIATE_TOOLS``, which lets this run even while a plan
session is in progress).

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

import asyncio
import re
from typing import Any, Callable

from nucore import NuCoreInterface
from utils import get_logger

logger = get_logger(__name__)

# Insteon/x10 addresses are conventionally written/spoken as three
# space-separated hex byte pairs (e.g. "AA BB CC"), but the hub's own node
# addresses always carry a fourth group-number token (e.g. "AA BB CC 1") --
# see normalize_address below.
_INSTEON_TRIPLE_RE = re.compile(r"^[0-9A-Fa-f]{2} [0-9A-Fa-f]{2} [0-9A-Fa-f]{2}$")
_INSTEON_QUAD_RE = re.compile(r"^[0-9A-Fa-f]{2} [0-9A-Fa-f]{2} [0-9A-Fa-f]{2} \S+$")


def normalize_address(original_address: str, protocol: str) -> str:
    """Normalize a device address to the hub's canonical form.

    Only insteon/x10 need this -- their addresses are three space-separated
    hex byte pairs (e.g. "AA BB CC") with an implied group/device number, but
    the hub always expects that number spelled out as a fourth token (e.g.
    "AA BB CC 1"). If *original_address* already has that fourth token, it's
    returned unchanged; if it's missing, "1" is appended. Anything that
    matches neither shape is returned unchanged with an error logged --
    never guessed at further. Every other protocol's addresses are returned
    unchanged (no known convention to normalize).
    """
    if protocol not in ("insteon", "x10"):
        return original_address

    if _INSTEON_QUAD_RE.match(original_address):
        return original_address
    if _INSTEON_TRIPLE_RE.match(original_address):
        return f"{original_address} 1"

    logger.error(
        f"'{original_address}' is not a recognized {protocol} address format "
        "(expected 'AA BB CC' or 'AA BB CC D') -- using it as-is"
    )
    return original_address


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

# add_device()/finish_device_discovery() are plain REST POSTs -- neither sets
# device_structure_changed itself. That only flips once the websocket's
# node-added event arrives (guaranteed, but async and not synchronous with
# the REST call above), and _refresh_device_structure() only actually
# reloads from the hub when that flag is already True. So we poll instead of
# assuming one call is enough -- worst case _REFRESH_MAX_ATTEMPTS *
# _REFRESH_POLL_TIMEOUT_S = 80s before giving up.
_REFRESH_POLL_INTERVAL_S = 2
_REFRESH_POLL_TIMEOUT_S = 40
_REFRESH_MAX_ATTEMPTS = 2


def _has_address(nucore_interface: NuCoreInterface, address: str) -> bool:
    """Case-insensitive membership check against nucore_interface.nodes --
    its keys come verbatim from the hub's own XML report (node_base.py just
    takes node_elem.find("./address").text, no case normalization), which
    isn't guaranteed to match whatever case the customer/model supplied for
    device_address (normalize_address only fixes the triple-vs-quad shape,
    not casing)."""
    target = address.casefold()
    return any(key.casefold() == target for key in nucore_interface.nodes)


async def _refresh_until(nucore_interface: NuCoreInterface, condition: Callable[[], bool]) -> bool:
    """Poll every ``_REFRESH_POLL_INTERVAL_S`` seconds, up to
    ``_REFRESH_MAX_ATTEMPTS * _REFRESH_POLL_TIMEOUT_S`` seconds total, for
    *condition()* to become true against ``nucore_interface``'s live state.

    Calls ``_refresh_device_structure()`` on every poll as a best-effort
    nudge, but checks *condition()* regardless of what it returns --
    ``nucore_interface`` is a shared, system-wide instance, not
    per-conversation, so something else may have already consumed
    ``device_structure_changed`` and reloaded before this loop got a turn;
    gating the condition check on *our own* call reporting a reload would
    miss a change that already landed via someone else's call.
    """
    total_budget_s = _REFRESH_MAX_ATTEMPTS * _REFRESH_POLL_TIMEOUT_S
    elapsed = 0
    while True:
        await nucore_interface._refresh_device_structure()
        if condition():
            return True
        if elapsed >= total_budget_s:
            return False
        await asyncio.sleep(_REFRESH_POLL_INTERVAL_S)
        elapsed += _REFRESH_POLL_INTERVAL_S


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

        device_address = normalize_address(device_address, protocol)
        found = await _refresh_until(nucore_interface, lambda: _has_address(nucore_interface, device_address))
        if not found:
            return {
                "error": (
                    f"'{device_address}' was added on the hub but never appeared locally after "
                    f"{_REFRESH_MAX_ATTEMPTS * _REFRESH_POLL_TIMEOUT_S}s of waiting -- try again shortly."
                )
            }
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
    before = set(nucore_interface.nodes.keys())
    ok = await nucore_interface.finish_device_discovery(flag=flag)
    if not ok:
        return {"error": "failed to commit inclusion session"}

    found_new = await _refresh_until(nucore_interface, lambda: set(nucore_interface.nodes.keys()) != before)
    new_addresses = sorted(set(nucore_interface.nodes.keys()) - before) if found_new else []
    return {
        "protocol": protocol, "action": action, "status": "inclusion_committed",
        "new_devices": [
            {"address": address, "name": nucore_interface.nodes[address].name} for address in new_addresses
        ],
        "note": "No new devices appeared during the inclusion window." if not new_addresses else
                "Rename/assign these as needed using the addresses above.",
    }

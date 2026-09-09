"""``pair_device`` -- add/pair or remove one physical device, as a
standalone global tool (not gated behind a Plan session -- see
``dispatch.py``'s ``_PLAN_ALWAYS_IMMEDIATE_TOOLS``, which lets this run even
while a plan session is in progress).

Three structurally different shapes, not one:
- ``add_by_address`` -- the customer already has/reads the device's own
  address (insteon/x10 only; z-wave/zigbee/matter devices have no usable
  address until physically activated during an inclusion window).
- ``start_inclusion``/``finish_inclusion`` -- put the controller in pairing
  (add) mode, the customer activates one or more devices, then a separate
  call commits everything included during that window. Available for
  insteon/zwave/zigbee/matter. ``start_inclusion`` snapshots the current node
  addresses *before* the pairing window opens (``_inclusion_baselines``) --
  a device the customer activates mid-window can already be reflected in
  ``nucore_interface.nodes`` (via the routine per-turn refresh) by the time
  the customer says "done" and ``finish_inclusion`` actually runs, so the
  diff has to be against that early snapshot, not one taken at
  ``finish_inclusion`` time.
- ``start_exclusion``/``finish_exclusion`` -- for removing a device instead
  of adding one. zwave/zigbee/matter only -- insteon has no distinct
  hardware "exclude" mode in this codebase. Unlike inclusion (the new
  device's address is unknown until it shows up), exclusion always targets
  an already-known, already-paired device, so both actions require
  ``device_address`` (the exact address from DEVICE DATABASE). Two different
  shapes hide behind these same two action names, per protocol:
  - zwave: a real two-step activation window, same shape as
    inclusion -- ``start_exclusion`` opens it, the customer physically
    activates the device, ``finish_exclusion`` commits and polls for that
    specific address to *disappear* from ``nucore_interface.nodes`` -- the
    mirror image of ``add_by_address`` polling for its address to *appear*
    (see ``_has_address``/``_refresh_until`` below).
  - zigbee/matter: no activation window exists at all -- either call
    directly and immediately removes the device via ``remove_device()``
    (mirrors eisy-ui's ``node/:address/remove``), so a single call (either
    one) is enough; see ``_remove_zmatter_device``.

insteon/zwave/zigbee/matter have real backends today (x10's only action,
add_by_address, is not implemented for it); every other protocol/action
combination that is nonetheless structurally valid (see
``_PROTOCOL_ACTIONS``) returns a "not yet supported" message rather than an
error, so the model falls back to walking the customer through manual
pairing. Z-Wave specifically only supports the Z-Matter hardware generation
-- a Legacy Z-Wave controller surfaces a clear error (raised by the backend
as ``NuCoreError``) instead of silently no-op'ing.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Callable

from nucore import NuCoreInterface, NuCoreError
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


_INCLUSION_EXCLUSION_ACTIONS = frozenset(
    {"start_inclusion", "finish_inclusion", "start_exclusion", "finish_exclusion"}
)

_PROTOCOL_ACTIONS: dict[str, frozenset[str]] = {
    "insteon": frozenset({"add_by_address", "start_inclusion", "finish_inclusion"}),
    "x10": frozenset({"add_by_address"}),
    "zwave": _INCLUSION_EXCLUSION_ACTIONS,
    "zigbee": _INCLUSION_EXCLUSION_ACTIONS,
    "matter": _INCLUSION_EXCLUSION_ACTIONS,
}

# zigbee/matter removal has no activation-window concept at all (unlike
# zwave) -- eisy-ui only ever calls a direct per-address remove endpoint
# for these two (ZIGBEE_REMOVE/MATTER_REMOVE, both `node/:address/remove`);
# there is no `node/exclude` for either. See _remove_zmatter_device.
_DIRECT_REMOVE_PROTOCOLS = frozenset({"zigbee", "matter"})

# Everything else in _PROTOCOL_ACTIONS is a real, valid protocol/action
# shape that just isn't backed by a real call yet.
_IMPLEMENTED_PROTOCOLS = frozenset({"insteon", "zwave", "zigbee", "matter"})

# Baseline node-address sets captured at start_inclusion time, keyed by
# protocol -- see finish_inclusion below. A device activated during the
# pairing window can already be reflected in nucore_interface.nodes (via the
# routine per-turn _refresh_device_structure() call made at the start of
# every conversation turn) well before the customer says "done" and
# finish_inclusion actually runs, so the before/after diff has to be against
# a snapshot taken before the window opened, not one taken at
# finish_inclusion time. A single shared cache keyed by protocol (not by
# conversation) matches the real hardware constraint -- the hub can only run
# one inclusion window per protocol at a time -- and nucore-ai only ever
# targets a single hub instance (see IoXWrapper._EISYUI_INSTANCE).
_inclusion_baselines: dict[str, set[str]] = {}

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


async def _remove_zmatter_device(
    nucore_interface: NuCoreInterface, protocol: str, action: str, device_address: str
) -> Any:
    """Zigbee/Matter device removal is one direct call, no activation
    window (unlike zwave's exclude mode -- see remove_device()). Shared by
    both start_exclusion and finish_exclusion so either one works by
    itself; calling both (out of habit, matching zwave's two-step shape) is
    harmless -- the second call just re-issues the same removal against an
    address that's typically already gone, and _refresh_until's condition
    is satisfied immediately.
    """
    # Snapshot the name up front -- it's unavailable once the device is
    # actually gone from nucore_interface.nodes.
    removed_name = next(
        (node.name for key, node in nucore_interface.nodes.items() if key.casefold() == device_address.casefold()),
        device_address,
    )
    try:
        ok = await nucore_interface.remove_device(device_address, protocol=protocol)
    except NuCoreError as e:
        return {"error": str(e)}
    if not ok:
        return {"error": f"failed to remove '{device_address}'"}

    removed = await _refresh_until(nucore_interface, lambda: not _has_address(nucore_interface, device_address))
    if not removed:
        return {
            "error": (
                f"'{device_address}' was still present after "
                f"{_REFRESH_MAX_ATTEMPTS * _REFRESH_POLL_TIMEOUT_S}s of waiting -- try again shortly."
            )
        }
    return {
        "protocol": protocol, "action": action, "status": "exclusion_committed",
        "removed_devices": [{"address": device_address, "name": removed_name}],
        "note": "Removed directly -- zigbee/matter devices don't need a pairing window or physical "
                "activation to be removed (unlike zwave); this one call was enough, no follow-up "
                "call needed.",
    }


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

    # protocol is one of _IMPLEMENTED_PROTOCOLS from here on. Only insteon
    # reaches add_by_address (it's the only implemented protocol with that
    # action in _PROTOCOL_ACTIONS).
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
        try:
            ok = await nucore_interface.discover_devices(
                device_type=args.get("device_type"), protocol=protocol, mode="include",
            )
        except NuCoreError as e:
            return {"error": str(e)}
        if not ok:
            return {"error": "failed to start include mode"}
        _inclusion_baselines[protocol] = set(nucore_interface.nodes.keys())
        return {
            "protocol": protocol, "action": action, "status": "inclusion_started",
            "note": "The hub is now in pairing mode -- tell the customer to activate each device "
                    "they want to add, then call finish_inclusion to commit.",
        }

    if action == "start_exclusion":
        device_address = args.get("device_address")
        if not device_address:
            return {"error": "device_address is required for start_exclusion"}
        if not _has_address(nucore_interface, device_address):
            return {
                "error": f"'{device_address}' is not a known device address -- check DEVICE "
                         "DATABASE for the real address rather than guessing"
            }
        if protocol in _DIRECT_REMOVE_PROTOCOLS:
            return await _remove_zmatter_device(nucore_interface, protocol, action, device_address)
        try:
            ok = await nucore_interface.discover_devices(
                device_type=args.get("device_type"), protocol=protocol, mode="exclude",
            )
        except NuCoreError as e:
            return {"error": str(e)}
        if not ok:
            return {"error": "failed to start exclude mode"}
        return {
            "protocol": protocol, "action": action, "device_address": device_address,
            "status": "exclusion_started",
            "note": f"The hub is now in removal mode -- tell the customer to activate the device "
                    f"at '{device_address}' now, then call finish_exclusion with this same "
                    f"device_address to commit.",
        }

    if action == "finish_inclusion":
        flag = args.get("flag", 1)
        # Pop the baseline captured at start_inclusion time -- see
        # _inclusion_baselines above. Falls back to a same-call snapshot only
        # if start_inclusion was never recorded for this protocol (e.g. a
        # process restart mid-session); that degrades to the old, racy
        # behavior rather than crashing, but is otherwise never hit in
        # normal use.
        before = _inclusion_baselines.pop(protocol, None)
        if before is None:
            before = set(nucore_interface.nodes.keys())
        try:
            ok = await nucore_interface.finish_device_discovery(flag=flag, protocol=protocol)
        except NuCoreError as e:
            return {"error": str(e)}
        if not ok:
            return {"error": "failed to commit inclusion session"}

        changed = await _refresh_until(nucore_interface, lambda: set(nucore_interface.nodes.keys()) != before)
        changed_addresses = sorted(set(nucore_interface.nodes.keys()) - before) if changed else []
        devices = [
            {"address": address, "name": nucore_interface.nodes[address].name} for address in changed_addresses
        ]
        return {
            "protocol": protocol, "action": action, "status": "inclusion_committed",
            "new_devices": devices,
            "note": "No new devices appeared during the inclusion window." if not devices else
                    "Rename/assign these as needed using the addresses above.",
        }

    # action == "finish_exclusion"
    device_address = args.get("device_address")
    if not device_address:
        return {"error": "device_address is required for finish_exclusion"}
    if protocol in _DIRECT_REMOVE_PROTOCOLS:
        return await _remove_zmatter_device(nucore_interface, protocol, action, device_address)
    # Snapshot the name up front -- it's unavailable once the device is
    # actually gone from nucore_interface.nodes.
    removed_name = next(
        (node.name for key, node in nucore_interface.nodes.items() if key.casefold() == device_address.casefold()),
        device_address,
    )
    flag = args.get("flag", 1)
    try:
        ok = await nucore_interface.finish_device_discovery(flag=flag, protocol=protocol)
    except NuCoreError as e:
        return {"error": str(e)}
    if not ok:
        return {"error": "failed to commit exclusion session"}

    removed = await _refresh_until(nucore_interface, lambda: not _has_address(nucore_interface, device_address))
    if not removed:
        return {
            "error": (
                f"'{device_address}' was still present after "
                f"{_REFRESH_MAX_ATTEMPTS * _REFRESH_POLL_TIMEOUT_S}s of waiting -- try again shortly."
            )
        }
    return {
        "protocol": protocol, "action": action, "status": "exclusion_committed",
        "removed_devices": [{"address": device_address, "name": removed_name}],
    }

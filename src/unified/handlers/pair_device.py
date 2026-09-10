"""``pair_device`` -- add/pair or remove one physical device, as a
standalone global tool (not gated behind a Plan session -- see
``dispatch.py``'s ``_PLAN_ALWAYS_IMMEDIATE_TOOLS``, which lets this run even
while a plan session is in progress).

Three actions, one call each -- no multi-turn "start, customer replies,
finish" shape for any protocol:
- ``add_by_address`` -- the customer already has/reads the device's own
  address (insteon/x10 only; z-wave/zigbee/matter devices have no usable
  address until physically activated during pairing). Waits (via
  ``wait_until``) for the address to actually become usable before
  returning.
- ``include`` -- add a device whose address isn't known up front (insteon/
  zwave/zigbee). Opens the hub's pairing mode, then blocks until that
  protocol's own "pairing session ended" event fires (or times out) -- then,
  since node creation can lag slightly behind that signal, gives the actual
  ``_3``/``ND`` (node added) event a brief grace period too (see
  ``_NODE_ADDED_GRACE_TIMEOUT_S``) before checking final state and reporting
  whatever new device(s) appeared. Each protocol signals session-end
  completely differently, confirmed against the real eisy-ui frontend
  (cloned from ``git@github.com:universaldevices/eisy-ui.git`` and read
  directly -- ``InsteonDiscoveryDialog.tsx``/``ZWaveDiscoveryDialog.tsx``/
  ``ZigbeeDiscoveryDialog.tsx``/``FamilyDiscoveryDialog.tsx``):
    - insteon: the customer finishes by clicking "Finish" in an on-screen
      eisy-ui dialog -- that click is what actually commits the session
      (with a flag the customer picks via UI radio buttons, never passed
      through chat). The dialog's own "COMPLETE" phase is reached on `_20`
      action `"2"` (`UD_LINKER_EVENT_CLEAR`, category `_20` = Linker
      Events) -- multi-device is real (the dialog tracks every device found
      before Finish is clicked), so waiting for this one terminal event
      rather than the first device appearing preserves that.
    - zwave/zigbee: driven by their own event category (`_25` zwave, `_27`
      zigbee), action format `"{category}.{type}"` -- sub-type `2` = include
      active, `1` = inactive (session ended), fired automatically by the hub,
      no customer UI click needed. The dialogs' own code comments state
      "Done: ... No cancel call" on the success path -- `finish_device_
      discovery`/`node/cancel` is only used to abort early and is never part
      of normal completion, so this call never makes it either.
    - matter: **not implemented via chat.** There is no simple `node/include`
      for Matter -- the real flow (`MatterInclusionDialog.tsx`) needs a
      customer-supplied pairing code/QR code and a 5-step commissioning
      sequence with no equivalent in this tool's arguments. `include` for
      matter returns a message directing the customer to the eisy-ui
      interface instead of attempting a call that doesn't correspond to
      anything real. Matter *exclusion* is unaffected -- see below.
- ``exclude`` -- remove an already-known, already-paired device (zwave/
  zigbee/matter only -- insteon has no distinct hardware "exclude" mode in
  this codebase). Two different shapes hide behind this one action name, per
  protocol:
  - zwave: ``device_address`` is optional -- the customer identifies the
    device physically (pressing its own exclude button while the hub
    listens), not by name in chat, so there is nothing to disambiguate
    up front. Opens exclude mode, then waits directly for the node-removed
    event (`_3`/`"NR"`) -- its own `node` field *is* the address of
    whichever device was just excluded, so no before/after diffing or
    ``_has_address`` re-check is needed to find out what happened.
  - zigbee/matter: no activation window exists at all -- confirmed no
    `node/exclude` exists for either protocol (only a direct per-address
    `node/:address/remove`, mirrored by ``remove_device()``) -- so
    ``device_address`` is required here, and this call alone performs the
    whole removal immediately; see ``_remove_zmatter_device``. Zigbee can
    fail silently at the network layer -- instead of actually removing the
    node, the hub sometimes just disables it locally, firing `_3`/`"EN"`
    (eventInfo `{"enabled": "false"}`) rather than `_3`/`"NR"`. This is
    detected (waiting for whichever of the two actually happens) and
    surfaced as an error telling the customer to remove it manually via
    eisy-ui, instead of reporting a false "success". Matter has no
    documented equivalent fallback, so it stays a plain wait for the
    address to disappear.

Every ``include``/``exclude`` call above blocks (up to ``_WAIT_TOTAL_TIMEOUT_S``)
waiting for the customer's physical action -- the turn loop streams the
model's own text live to the customer as it's generated, including text
that precedes a ``tool_use`` block in the same round, *before* that tool
call executes (see ``claude_adapter.py``). So the model must say
instructions to the customer ("follow the on-screen instructions...") as
its own text *immediately before* calling this tool, in the same reply --
not after, and never expecting the customer to reply in chat to continue --
since only text preceding a tool call reaches the customer while the call
is still running.

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

import re
from typing import Any

from nucore import NuCoreInterface, NuCoreError
from utils import get_logger

from ._event_wait import wait_for_event, wait_for_matching_event, wait_for_node_event, wait_until

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
    "insteon": frozenset({"add_by_address", "include"}),
    "x10": frozenset({"add_by_address"}),
    "zwave": frozenset({"include", "exclude"}),
    "zigbee": frozenset({"include", "exclude"}),
    "matter": frozenset({"include", "exclude"}),
}

# Each protocol's own "pairing session ended" event -- (control, action) --
# confirmed against the real eisy-ui frontend (see module docstring). This
# is what `include` waits for; matter isn't here because `include` never
# reaches discover_devices() for it at all (see module docstring).
_INCLUDE_COMPLETE_EVENT: dict[str, tuple[str, str]] = {
    "insteon": ("_20", "2"),
    "zwave": ("_25", "2.1"),
    "zigbee": ("_27", "2.1"),
}

# zigbee/matter removal has no activation-window concept at all (unlike
# zwave) -- eisy-ui only ever calls a direct per-address remove endpoint
# for these two (ZIGBEE_REMOVE/MATTER_REMOVE, both `node/:address/remove`);
# there is no `node/exclude` for either. See _remove_zmatter_device.
_DIRECT_REMOVE_PROTOCOLS = frozenset({"zigbee", "matter"})

# Everything else in _PROTOCOL_ACTIONS is a real, valid protocol/action
# shape that just isn't backed by a real call yet.
_IMPLEMENTED_PROTOCOLS = frozenset({"insteon", "zwave", "zigbee", "matter"})

# add_device()/discover_devices() are plain REST calls -- neither updates
# the local node list itself. That only happens once the websocket reports
# the relevant event (guaranteed, but async and not synchronous with the
# REST call above). So instead of assuming one refresh is enough, we wait --
# event-driven, via wait_until/wait_for_event -- worst case
# _WAIT_TOTAL_TIMEOUT_S before giving up.
_WAIT_TOTAL_TIMEOUT_S = 80

# include's own pairing-session-ended event (_INCLUDE_COMPLETE_EVENT) can
# fire slightly before the node manager actually finishes creating the new
# node's record -- once the session itself has ended, this is just a brief
# grace period for the real _3/ND (node added) event to land before the
# final state check, not another "wait for the customer" budget.
_NODE_ADDED_GRACE_TIMEOUT_S = 60


def _has_address(nucore_interface: NuCoreInterface, address: str) -> bool:
    """Case-insensitive membership check against nucore_interface.nodes --
    its keys come verbatim from the hub's own XML report (node_base.py just
    takes node_elem.find("./address").text, no case normalization), which
    isn't guaranteed to match whatever case the customer/model supplied for
    device_address (normalize_address only fixes the triple-vs-quad shape,
    not casing)."""
    target = address.casefold()
    return any(key.casefold() == target for key in nucore_interface.nodes)


def _get_node_case_insensitive(nucore_interface: NuCoreInterface, address: str):
    target = address.casefold()
    return next((node for key, node in nucore_interface.nodes.items() if key.casefold() == target), None)


def _is_device_usable(nucore_interface: NuCoreInterface, address: str) -> bool:
    """True once *address* exists locally AND its device profile has been
    resolved -- ``node.node_def`` is not ``None``.

    The hub can register a bare node -- firing ``_3``/``"ND"`` -- well
    before it finishes the Insteon engine-version/product-data handshake
    that determines its nodedef (``_3``/``"NI"``, a separate, later event --
    see subscription_events.md's "Key Event: `_3/NI`" section). Until that
    lands, ``node.node_def`` stays ``None`` and ``Profile.map_nodes()``
    (nucore/profile.py) never populated it with any commands/properties --
    exactly what ``create_or_update_routine``'s resolver ("has no device
    profile to resolve...") and ``multi_device_scene``'s role precheck
    ("not available as a controller/responder") both reject. Mere presence
    (what ``_has_address`` checks) is not the same as usable by either of
    those, even though the address already "exists".

    Also requires ``nucore_interface.system_busy`` to be ``False`` --
    according to subscription_events.md, ``_5`` (System Busy Events) action
    ``"0"``/``"1"`` is the hub's own not-busy/busy signal, set by
    ``IoXWrapper._on_device_event``. The same hardware handshake that
    resolves a new node's profile can leave the whole system reporting busy
    a moment longer -- referencing the device in a scene/routine call while
    that's still true is the same kind of premature "usable" claim as
    ``node_def`` still being unresolved.
    """
    node = _get_node_case_insensitive(nucore_interface, address)
    return node is not None and node.node_def is not None and not nucore_interface.system_busy


def _is_zigbee_disabled_event(node: str, control: str, action: str, event_info: object) -> bool:
    """Zigbee removal can fail at the network layer without the REST call
    itself reporting it -- instead of actually removing the node, the hub
    sometimes just disables it locally and fires ``_3``/``"EN"`` (node
    enabled/disabled) with ``eventInfo == {"enabled": "false"}``, never
    ``_3``/``"NR"`` (node removed) at all."""
    return action == "EN" and isinstance(event_info, dict) and event_info.get("enabled") == "false"


async def _remove_zmatter_device(
    nucore_interface: NuCoreInterface, protocol: str, action: str, device_address: str
) -> Any:
    """Zigbee/Matter device removal is one direct call, no activation
    window (unlike zwave's exclude mode -- see remove_device()). This is
    the only ``exclude`` path for these two protocols -- neither has a
    windowed `node/exclude` at all (confirmed against the real eisy-ui
    frontend -- see module docstring).
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

    if protocol == "zigbee":
        # Wait for whichever of the two real outcomes the hub reports --
        # an actual removal (NR) or a silent fallback to merely disabling
        # the node (EN, enabled=false) -- rather than inferring success
        # purely from the node disappearing (a disabled-but-not-removed
        # node may or may not still show up locally).
        event = await wait_for_matching_event(
            nucore_interface, "_3",
            lambda node, control, evt_action, event_info: evt_action == "NR" or _is_zigbee_disabled_event(node, control, evt_action, event_info),
            _WAIT_TOTAL_TIMEOUT_S,
        )
        await nucore_interface._refresh_device_structure()
        if event is None:
            return {
                "error": (
                    f"'{device_address}' was still present after "
                    f"{_WAIT_TOTAL_TIMEOUT_S}s of waiting -- try again shortly."
                )
            }
        _node, _control, matched_action, _event_info = event
        if matched_action == "EN":
            return {
                "error": (
                    f"'{removed_name}' ({device_address}) could not be removed from the Zigbee "
                    "network -- the hub only disabled it locally instead. The customer needs to "
                    "remove it manually from the eisy-ui interface."
                ),
                "device_address": device_address,
            }
        return {
            "protocol": protocol, "action": action, "status": "exclusion_committed",
            "removed_devices": [{"address": device_address, "name": removed_name}],
            "note": "Removed directly -- zigbee devices don't need a pairing window or physical "
                    "activation to be removed (unlike zwave); this one call was enough, no follow-up "
                    "call needed.",
        }

    # matter -- no documented "disabled instead of removed" fallback, so
    # this stays a plain wait for the address to actually disappear.
    removed = await wait_until(
        nucore_interface, "_3", None,
        lambda: not _has_address(nucore_interface, device_address),
        nucore_interface._refresh_device_structure,
        _WAIT_TOTAL_TIMEOUT_S,
    )
    if not removed:
        return {
            "error": (
                f"'{device_address}' was still present after "
                f"{_WAIT_TOTAL_TIMEOUT_S}s of waiting -- try again shortly."
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
        # Wildcard, not just "ND" -- becoming usable takes two separate
        # events (_3/ND for bare presence, then a later _3/NI once its
        # device profile resolves -- see _is_device_usable), and narrowing
        # to one action risks never waking for the other.
        found = await wait_until(
            nucore_interface, "_3", None,
            lambda: _is_device_usable(nucore_interface, device_address),
            nucore_interface._refresh_device_structure,
            _WAIT_TOTAL_TIMEOUT_S,
        )
        if not found:
            return {
                "error": (
                    f"'{device_address}' was added on the hub but never became fully usable "
                    f"(address present and device profile resolved) after {_WAIT_TOTAL_TIMEOUT_S}s "
                    "of waiting -- try again shortly."
                )
            }
        return {"protocol": protocol, "action": action, "device_address": result, "status": "added"}

    if action == "include":
        if protocol == "matter":
            return (
                "Adding a Matter device requires scanning a QR code or entering a pairing code, "
                "which isn't available through this chat -- please use the eisy-ui interface "
                "directly (Devices -> Add Device -> Matter) to add this device."
            )
        try:
            ok = await nucore_interface.discover_devices(
                device_type=args.get("device_type"), protocol=protocol, mode="include",
            )
        except NuCoreError as e:
            return {"error": str(e)}
        if not ok:
            return {"error": "failed to start include mode"}

        before = set(nucore_interface.nodes.keys())
        control, complete_action = _INCLUDE_COMPLETE_EVENT[protocol]
        await wait_for_event(nucore_interface, control, complete_action, _WAIT_TOTAL_TIMEOUT_S)
        # The pairing session has ended -- give the actual node-added event a
        # brief grace period to land too, in case node creation lags slightly
        # behind the session-ended signal (see _NODE_ADDED_GRACE_TIMEOUT_S).
        await wait_for_event(nucore_interface, "_3", "ND", _NODE_ADDED_GRACE_TIMEOUT_S)
        await nucore_interface._refresh_device_structure()

        changed_addresses = sorted(set(nucore_interface.nodes.keys()) - before)
        if changed_addresses:
            # A newly-added node's own device profile (node_def) can resolve
            # after this point -- see _is_device_usable -- so give that a
            # further grace period too before handing addresses back as
            # ready to reference in the very next tool call.
            await wait_until(
                nucore_interface, "_3", None,
                lambda: all(_is_device_usable(nucore_interface, a) for a in changed_addresses),
                nucore_interface._refresh_device_structure,
                _NODE_ADDED_GRACE_TIMEOUT_S,
            )
        devices = [
            {"address": address, "name": nucore_interface.nodes[address].name} for address in changed_addresses
        ]
        return {
            "protocol": protocol, "action": action, "status": "inclusion_committed",
            "new_devices": devices,
            "note": "No new devices appeared during the inclusion window." if not devices else
                    "Rename/assign these as needed using the addresses above.",
        }

    # action == "exclude"
    device_address = args.get("device_address")

    if protocol in _DIRECT_REMOVE_PROTOCOLS:
        # zigbee/matter target one specific address directly (no physical
        # activation window to identify the device some other way), so an
        # address is genuinely required here.
        if not device_address:
            return {"error": "device_address is required for exclude"}
        if not _has_address(nucore_interface, device_address):
            return {
                "error": f"'{device_address}' is not a known device address -- check DEVICE "
                         "DATABASE for the real address rather than guessing"
            }
        return await _remove_zmatter_device(nucore_interface, protocol, action, device_address)

    # zwave only, from here -- device_address is optional: the customer
    # identifies the device physically (pressing its exclude button while
    # the hub is listening), and the hub reports back which one it was via
    # the node-removed event -- there's nothing to disambiguate in chat, so
    # never ask the customer which device before calling this.
    if device_address and not _has_address(nucore_interface, device_address):
        return {
            "error": f"'{device_address}' is not a known device address -- check DEVICE "
                     "DATABASE for the real address rather than guessing"
        }
    try:
        ok = await nucore_interface.discover_devices(
            device_type=args.get("device_type"), protocol=protocol, mode="exclude",
        )
    except NuCoreError as e:
        return {"error": str(e)}
    if not ok:
        return {"error": "failed to start exclude mode"}

    # The node-removed event's own "node" field *is* the address of the
    # device that was excluded -- no before/after diffing or _has_address
    # re-check needed (that older pattern predates real event handling).
    # Its name is looked up before the refresh below removes it locally.
    removed_address = await wait_for_node_event(nucore_interface, "_3", "NR", _WAIT_TOTAL_TIMEOUT_S)
    if removed_address is None:
        return {
            "error": (
                f"no device was excluded after {_WAIT_TOTAL_TIMEOUT_S}s of waiting -- try again shortly."
            )
        }
    removed_name = next(
        (node.name for key, node in nucore_interface.nodes.items() if key.casefold() == removed_address.casefold()),
        removed_address,
    )
    await nucore_interface._refresh_device_structure()

    return {
        "protocol": protocol, "action": action, "status": "exclusion_committed",
        "removed_devices": [{"address": removed_address, "name": removed_name}],
    }

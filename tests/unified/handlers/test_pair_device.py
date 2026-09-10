"""``pair_device`` -- standalone global tool, not gated behind a Plan
session. Covers: the protocol/action validity table (structural rejection
vs. not-yet-supported), add_by_address, the unified include/exclude actions
across insteon/zwave/zigbee/matter, the Legacy-Z-Wave error carve-out, and
that the tool stays callable via dispatch.execute_tool while a plan session
is running for the owning session_id (confirms dispatch.py's
_PLAN_ALWAYS_IMMEDIATE_TOOLS/_SESSION_SCOPED_TOOLS split doesn't crash on an
unexpected session_id kwarg).

Also covers the event-driven waits in pair_device.py's use of
wait_until/wait_for_event/wait_for_node_event (_event_wait.py): add_device()
is a plain REST call that doesn't itself update the local node list, so
add_by_address waits for the newly-added device to actually show up locally.
include instead waits for each protocol's own documented "pairing session
ended" event (_20/"2" insteon, _25/"2.1" zwave, _27/"2.1" zigbee) --
confirmed against the real eisy-ui frontend. zwave exclude waits directly
for the node-removed event (_3/"NR") and takes the removed address straight
from that event's own node field -- no before/after diffing or
_has_address re-check needed. finish_device_discovery is never called by
any of these flows (confirmed not part of the real success path for any
protocol), so FakeBackend's implementation of it deliberately raises if
ever invoked, to catch a regression loudly instead of silently returning
success.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from nucore.nucore_error import NuCoreError
from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool
from unified.handlers import pair_device as pair_device_module
from unified.handlers.pair_device import normalize_address, pair_device


@pytest.fixture(autouse=True)
def _fast_wait_timeout(monkeypatch):
    # wait_until()/wait_for_event() genuinely block (via asyncio.to_thread)
    # rather than asyncio.sleep()-ing, so they can't be mocked away like a
    # fixed poll interval could -- shrink both budgets instead, so a test
    # whose event never arrives still gives up fast rather than for real
    # seconds.
    monkeypatch.setattr(pair_device_module, "_WAIT_TOTAL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(pair_device_module, "_NODE_ADDED_GRACE_TIMEOUT_S", 0.1)


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.add_device_calls: list[tuple] = []
        self.discover_calls: list[tuple] = []
        self.remove_device_calls: list[tuple] = []
        self.add_device_result: object = "1A 2B 3C 1"
        self.discover_result: bool = True
        self.remove_device_result: bool = True
        self.discover_error: Exception | None = None
        self.remove_device_error: Exception | None = None
        self.refresh_calls = 0
        # add_device stages the address here, and the default
        # _refresh_device_structure below merges it into self.nodes on its
        # first call -- simulates a real hub reload finding what was just
        # added, without needing every existing test to script the refresh
        # itself.
        self._pending_nodes: dict[str, str] = {}
        # remove_device stages the address here, consumed by
        # _refresh_device_structure the same way -- simulates the hub
        # reflecting a direct zigbee/matter removal on its next reload.
        self._pending_removals: set[str] = set()

    async def add_device(self, device_address, name=None, device_type=None, **kwargs):
        self.add_device_calls.append((device_address, name, device_type))
        if self.add_device_result is not None:
            self._pending_nodes[device_address] = name or device_address
        return self.add_device_result

    async def discover_devices(self, device_type=None, protocol=None, mode="include", **kwargs):
        if self.discover_error is not None:
            raise self.discover_error
        self.discover_calls.append((device_type, protocol, mode))
        return self.discover_result

    async def finish_device_discovery(self, flag=1, protocol=None, **kwargs):
        # Confirmed (against the real eisy-ui frontend) not part of the
        # real success path for include/exclude, for any protocol -- raising
        # here means a regression that starts calling it again fails loudly
        # instead of silently "working".
        raise AssertionError("finish_device_discovery must not be called by include/exclude")

    async def remove_device(self, device_address, protocol=None, **kwargs):
        if self.remove_device_error is not None:
            raise self.remove_device_error
        self.remove_device_calls.append((device_address, protocol))
        if self.remove_device_result:
            self._pending_removals.add(device_address)
        return self.remove_device_result

    async def _refresh_device_structure(self):
        # Always reports a successful (instant) reload -- merges in whatever
        # add_device staged, if anything. Returning True unconditionally
        # keeps tests that don't care about the refresh mechanics from
        # blocking on wait_until's real wait loop.
        self.refresh_calls += 1
        for address, name in self._pending_nodes.items():
            self.nodes[address] = SimpleNamespace(name=name, node_def=object())
        self._pending_nodes = {}
        for address in self._pending_removals:
            self.nodes.pop(address, None)
        self._pending_removals = set()
        return True

    async def run_diagnostic_step(self, step, **params): raise NotImplementedError

    async def _load(self, **kwargs): raise NotImplementedError
    async def _load_routines(self): raise NotImplementedError
    async def _load_variables(self): pass
    async def send_commands(self, commands): raise NotImplementedError
    async def create_automation_routine(self, trigger): raise NotImplementedError
    async def update_routine(self, program): raise NotImplementedError
    async def get_routine(self, routine_id): raise NotImplementedError
    async def get_properties(self, device_id): raise NotImplementedError
    def get_device_name(self, device_id): raise NotImplementedError
    def get_device_id(self, device_str): raise NotImplementedError
    async def get_all_routines_summary(self): raise NotImplementedError
    async def get_routine_summary(self, routine_id): raise NotImplementedError
    async def get_all_routines(self): raise NotImplementedError
    async def add_node(self, node_name, type): raise NotImplementedError
    async def node_ops(self, node_id, operation, **kwargs): raise NotImplementedError
    async def routine_ops(self, routine_id, operation): raise NotImplementedError
    async def variable_ops(self, var_type, var_id, operation, **kwargs): raise NotImplementedError
    async def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_update_link(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_node_roles(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError


async def _fire_complete_event_shortly(backend: FakeBackend, protocol: str, delay: float = 0.02):
    """Simulates the hub's own "pairing session ended" event arriving --
    the same (control, action) pair pair_device.py registers for via
    _INCLUDE_COMPLETE_EVENT."""
    await asyncio.sleep(delay)
    control, action = pair_device_module._INCLUDE_COMPLETE_EVENT[protocol]
    backend._dispatch_event_listeners(control, action, None, {})


async def _fire_node_removed_event_shortly(backend: FakeBackend, node_address: str, delay: float = 0.02):
    """Simulates the hub's own node-removed event (_3/"NR") arriving --
    zwave exclude registers for this directly and reads the removed address
    straight out of the event's own node field."""
    await asyncio.sleep(delay)
    backend._dispatch_event_listeners("_3", "NR", node_address, {})


# ---------------------------------------------------------------------------
# normalize_address
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("protocol", ["insteon", "x10"])
def test_normalize_address_appends_default_group_to_a_bare_triple(protocol):
    assert normalize_address("1A 2B 3C", protocol) == "1A 2B 3C 1"


@pytest.mark.parametrize("protocol", ["insteon", "x10"])
def test_normalize_address_leaves_an_existing_quad_unchanged(protocol):
    assert normalize_address("1A 2B 3C 2", protocol) == "1A 2B 3C 2"


@pytest.mark.parametrize("protocol", ["insteon", "x10"])
def test_normalize_address_leaves_an_unrecognized_shape_unchanged_and_logs(protocol, caplog):
    with caplog.at_level("ERROR"):
        result = normalize_address("not-an-address", protocol)
    assert result == "not-an-address"
    assert "not a recognized" in caplog.text


@pytest.mark.parametrize("protocol", ["zwave", "zigbee", "matter"])
def test_normalize_address_is_a_no_op_for_other_protocols(protocol):
    assert normalize_address("1A 2B 3C", protocol) == "1A 2B 3C"
    assert normalize_address("anything at all", protocol) == "anything at all"


# ---------------------------------------------------------------------------
# add_by_address -- insteon/x10 only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_by_address_succeeds():
    backend = FakeBackend()
    result = await pair_device(backend, {
        "protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })
    assert result == {
        "protocol": "insteon", "action": "add_by_address",
        "device_address": "1A 2B 3C 1", "status": "added",
    }
    assert backend.add_device_calls == [("1A 2B 3C 1", None, None)]


@pytest.mark.asyncio
async def test_add_by_address_requires_device_address():
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": "insteon", "action": "add_by_address"})
    assert "error" in result
    assert backend.add_device_calls == []


@pytest.mark.asyncio
async def test_add_by_address_reports_failure():
    backend = FakeBackend()
    backend.add_device_result = None
    result = await pair_device(backend, {
        "protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })
    assert "error" in result


@pytest.mark.asyncio
async def test_add_by_address_matches_the_hub_reported_address_case_insensitively():
    # The hub's own XML report can come back in a different case than
    # whatever the customer/model typed -- the presence check must not
    # treat that as "not found yet".
    backend = FakeBackend()

    async def reports_lowercase_address():
        backend.nodes["1a 2b 3c 1"] = SimpleNamespace(name="1a 2b 3c 1", node_def=object())
        return True

    backend._refresh_device_structure = reports_lowercase_address

    result = await pair_device(backend, {
        "protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })

    assert result["status"] == "added"


@pytest.mark.asyncio
async def test_add_by_address_waits_for_an_event_before_succeeding():
    # The device doesn't show up on the very first refresh -- wait_until
    # must actually block until a matching device event wakes it (not just
    # check once and give up), then refresh again and find it.
    backend = FakeBackend()
    calls = {"n": 0}

    async def flaky_refresh():
        calls["n"] += 1
        if calls["n"] >= 2:
            backend.nodes["1A 2B 3C 1"] = SimpleNamespace(name="1A 2B 3C 1", node_def=object())
        return True

    backend._refresh_device_structure = flaky_refresh

    async def fire_event_shortly():
        await asyncio.sleep(0.02)
        backend._dispatch_event_listeners("_3", "ND", "1A 2B 3C 1", {})

    asyncio.create_task(fire_event_shortly())

    result = await pair_device(backend, {
        "protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })

    assert result["status"] == "added"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_add_by_address_waits_for_device_profile_not_just_bare_address():
    # Regression: the hub can register a bare node (address present,
    # node.node_def still None) well before it finishes resolving the
    # device's profile via a later _3/"NI" event -- returning "added" at the
    # first point only handed back a device that create_or_update_routine's
    # resolver and multi_device_scene's role precheck would then reject as
    # having "no device profile"/"not available as a controller".
    backend = FakeBackend()

    async def address_appears_without_a_profile_yet():
        # Only creates the node once -- a real refresh reloads from the hub,
        # it doesn't reset an already-resolved node_def back to None.
        backend.nodes.setdefault("1A 2B 3C 1", SimpleNamespace(name="1A 2B 3C 1", node_def=None))
        return True

    backend._refresh_device_structure = address_appears_without_a_profile_yet

    async def profile_resolves_shortly():
        await asyncio.sleep(0.02)
        backend.nodes["1A 2B 3C 1"].node_def = object()
        backend._dispatch_event_listeners("_3", "NI", "1A 2B 3C 1", {})

    asyncio.create_task(profile_resolves_shortly())

    result = await pair_device(backend, {
        "protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })

    assert result["status"] == "added"


@pytest.mark.asyncio
async def test_add_by_address_times_out_if_the_profile_never_resolves():
    backend = FakeBackend()

    async def address_present_but_profile_never_resolves():
        backend.nodes["1A 2B 3C 1"] = SimpleNamespace(name="1A 2B 3C 1", node_def=None)
        return True

    backend._refresh_device_structure = address_present_but_profile_never_resolves

    result = await pair_device(backend, {
        "protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })

    assert "error" in result
    assert "device profile" in result["error"]


@pytest.mark.asyncio
async def test_add_by_address_waits_for_the_system_to_stop_reporting_busy():
    # Regression: node.node_def resolving isn't the only signal that a
    # device is actually safe to reference -- the hub's own _5 (System Busy
    # Events) can still report busy a moment longer during the same
    # handshake. _is_device_usable must gate on nucore_interface.system_busy
    # too, not just address presence + node_def.
    backend = FakeBackend()
    backend.system_busy = True
    backend.nodes["1A 2B 3C 1"] = SimpleNamespace(name="1A 2B 3C 1", node_def=object())

    async def system_becomes_not_busy_shortly():
        await asyncio.sleep(0.02)
        backend.system_busy = False
        backend._dispatch_event_listeners("_3", "NI", "1A 2B 3C 1", {})

    asyncio.create_task(system_becomes_not_busy_shortly())

    result = await pair_device(backend, {
        "protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })

    assert result["status"] == "added"


@pytest.mark.asyncio
async def test_add_by_address_times_out_while_the_system_stays_busy():
    backend = FakeBackend()
    backend.system_busy = True
    backend.nodes["1A 2B 3C 1"] = SimpleNamespace(name="1A 2B 3C 1", node_def=object())

    result = await pair_device(backend, {
        "protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })

    assert "error" in result


@pytest.mark.asyncio
async def test_add_by_address_errors_if_the_device_never_appears():
    # Refresh keeps "succeeding" and no event ever arrives, but the target
    # address never actually shows up -- wait_until must give up once
    # _WAIT_TOTAL_TIMEOUT_S elapses.
    backend = FakeBackend()

    async def refreshes_but_finds_nothing():
        return True

    backend._refresh_device_structure = refreshes_but_finds_nothing

    result = await pair_device(backend, {
        "protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })

    assert "error" in result


# ---------------------------------------------------------------------------
# Not-yet-supported protocols -- structurally valid action, just not backed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_x10_add_by_address_is_not_yet_supported():
    backend = FakeBackend()
    result = await pair_device(backend, {
        "protocol": "x10", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })
    assert "not yet supported" in result


# ---------------------------------------------------------------------------
# include -- insteon/zwave/zigbee open pairing mode and block for the
# protocol's own "session ended" event; matter is redirected to the UI.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["insteon", "zwave", "zigbee"])
async def test_include_opens_pairing_mode_and_reports_a_new_device(protocol):
    backend = FakeBackend()
    asyncio.create_task(_fire_complete_event_shortly(backend, protocol))

    async def adds_a_device():
        backend.nodes["NEW001"] = SimpleNamespace(name="NEW001", node_def=object())
        return True

    backend._refresh_device_structure = adds_a_device

    result = await pair_device(backend, {"protocol": protocol, "action": "include"})

    assert backend.discover_calls == [(None, protocol, "include")]
    assert result["status"] == "inclusion_committed"
    assert [d["address"] for d in result["new_devices"]] == ["NEW001"]


@pytest.mark.asyncio
async def test_include_reports_every_device_added_during_the_window_for_insteon():
    # Insteon's pairing window is multi-device -- several devices can appear
    # before the customer clicks Finish on their screen (the _20/"2" event).
    # Waiting for that one terminal event, not the first device, must report
    # all of them.
    backend = FakeBackend()

    async def adds_two_devices_then_completes():
        backend.nodes["11 11 11 1"] = SimpleNamespace(name="11 11 11 1", node_def=object())
        backend.nodes["22 22 22 1"] = SimpleNamespace(name="22 22 22 1", node_def=object())
        await asyncio.sleep(0.02)
        backend._dispatch_event_listeners("_20", "2", None, {})

    asyncio.create_task(adds_two_devices_then_completes())

    result = await pair_device(backend, {"protocol": "insteon", "action": "include"})

    assert result["status"] == "inclusion_committed"
    assert {d["address"] for d in result["new_devices"]} == {"11 11 11 1", "22 22 22 1"}


@pytest.mark.asyncio
async def test_include_waits_for_the_node_added_event_after_the_session_ends():
    # The pairing session can end (_25/"2.1") slightly before the node
    # manager actually finishes creating the node's record -- the
    # _3/ND grace-period wait must give it a moment to land before the final
    # state check runs, not check immediately once the session ends.
    backend = FakeBackend()

    async def session_ends_then_node_added_slightly_later():
        await asyncio.sleep(0.01)
        backend._dispatch_event_listeners("_25", "2.1", None, {})
        await asyncio.sleep(0.03)  # within the (monkeypatched) 0.1s grace window
        backend.nodes["ZW001"] = SimpleNamespace(name="ZW001", node_def=object())
        backend._dispatch_event_listeners("_3", "ND", "ZW001", {})

    asyncio.create_task(session_ends_then_node_added_slightly_later())

    result = await pair_device(backend, {"protocol": "zwave", "action": "include"})

    assert [d["address"] for d in result["new_devices"]] == ["ZW001"]


@pytest.mark.asyncio
async def test_include_waits_for_the_new_devices_profile_too():
    # Regression, same underlying issue as add_by_address: the node-added
    # grace period only confirms the address exists, not that its device
    # profile (node_def) has resolved yet (a later, separate _3/"NI" event)
    # -- a device handed back before that point is unusable by
    # create_or_update_routine/multi_device_scene right afterward.
    backend = FakeBackend()

    async def node_added_then_profile_resolves_slightly_later():
        await asyncio.sleep(0.01)
        backend._dispatch_event_listeners("_25", "2.1", None, {})
        backend.nodes["ZW001"] = SimpleNamespace(name="ZW001", node_def=None)
        backend._dispatch_event_listeners("_3", "ND", "ZW001", {})
        await asyncio.sleep(0.03)  # within the (monkeypatched) 0.1s grace window
        backend.nodes["ZW001"].node_def = object()
        backend._dispatch_event_listeners("_3", "NI", "ZW001", {})

    asyncio.create_task(node_added_then_profile_resolves_slightly_later())

    result = await pair_device(backend, {"protocol": "zwave", "action": "include"})

    assert [d["address"] for d in result["new_devices"]] == ["ZW001"]
    assert backend.nodes["ZW001"].node_def is not None


@pytest.mark.asyncio
async def test_include_does_not_error_if_the_new_devices_profile_never_resolves():
    # Best-effort grace period, not a hard requirement -- a device whose
    # profile is still resolving when the budget runs out is still reported
    # (same as before this fix), not turned into an error.
    backend = FakeBackend()

    async def node_added_but_profile_never_resolves():
        await asyncio.sleep(0.01)
        backend._dispatch_event_listeners("_25", "2.1", None, {})
        backend.nodes["ZW001"] = SimpleNamespace(name="ZW001", node_def=None)
        backend._dispatch_event_listeners("_3", "ND", "ZW001", {})

    asyncio.create_task(node_added_but_profile_never_resolves())

    result = await pair_device(backend, {"protocol": "zwave", "action": "include"})

    assert [d["address"] for d in result["new_devices"]] == ["ZW001"]
    assert "error" not in result


@pytest.mark.asyncio
async def test_include_reports_nothing_new_without_erroring_on_timeout():
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": "zwave", "action": "include"})
    assert result["status"] == "inclusion_committed"
    assert result["new_devices"] == []
    assert "error" not in result


@pytest.mark.asyncio
async def test_include_does_not_wake_on_a_differently_actioned_event():
    # wait_for_event registers on the EXACT completion action -- an event on
    # the same control but a different action (unlike wait_until's wildcard
    # support) must not satisfy it.
    backend = FakeBackend()

    async def fire_wrong_action():
        await asyncio.sleep(0.02)
        backend._dispatch_event_listeners("_25", "2.2", None, {})  # "include active", not "2.1" inactive

    asyncio.create_task(fire_wrong_action())

    result = await pair_device(backend, {"protocol": "zwave", "action": "include"})

    assert result["new_devices"] == []  # timed out despite the (irrelevant) event


@pytest.mark.asyncio
async def test_include_reports_failure_to_start_pairing_mode():
    backend = FakeBackend()
    backend.discover_result = False
    result = await pair_device(backend, {"protocol": "zwave", "action": "include"})
    assert "error" in result


@pytest.mark.asyncio
async def test_include_surfaces_a_nucore_error_from_discover_devices():
    backend = FakeBackend()
    backend.discover_error = NuCoreError("something went wrong")
    result = await pair_device(backend, {"protocol": "zigbee", "action": "include"})
    assert result == {"error": "something went wrong"}


@pytest.mark.asyncio
async def test_include_for_matter_redirects_to_the_ui_instead_of_calling_discover_devices():
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": "matter", "action": "include"})
    assert "eisy-ui" in result
    assert backend.discover_calls == []


# ---------------------------------------------------------------------------
# exclude -- zwave opens removal mode then waits directly for the
# node-removed event (_3/"NR"), reading the removed address out of that
# event rather than diffing/re-checking node state; zigbee/matter remove
# directly, one call, no window.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exclude_opens_removal_mode_and_reports_removal_for_zwave():
    backend = FakeBackend()
    backend.nodes["ZW001"] = SimpleNamespace(name="Front Door Lock")
    asyncio.create_task(_fire_node_removed_event_shortly(backend, "ZW001"))

    result = await pair_device(
        backend, {"protocol": "zwave", "action": "exclude", "device_address": "ZW001"}
    )

    assert backend.discover_calls == [(None, "zwave", "exclude")]
    assert result["status"] == "exclusion_committed"
    assert result["removed_devices"] == [{"address": "ZW001", "name": "Front Door Lock"}]
    assert backend.remove_device_calls == []


@pytest.mark.asyncio
async def test_exclude_uses_the_address_carried_by_the_node_removed_event():
    # The event's own node field is authoritative -- not a diff or a
    # re-check of the originally requested device_address.
    backend = FakeBackend()
    backend.nodes["ZW001"] = SimpleNamespace(name="Front Door Lock")
    asyncio.create_task(_fire_node_removed_event_shortly(backend, "ZW001"))

    result = await pair_device(
        backend, {"protocol": "zwave", "action": "exclude", "device_address": "ZW001"}
    )

    assert result["removed_devices"][0]["address"] == "ZW001"


@pytest.mark.asyncio
async def test_exclude_works_for_zwave_without_a_device_address():
    # The customer identifies the device physically (activating it while
    # the hub listens) -- there's nothing to disambiguate in chat, so
    # device_address must not be required here.
    backend = FakeBackend()
    backend.nodes["ZW001"] = SimpleNamespace(name="Front Door Lock")
    asyncio.create_task(_fire_node_removed_event_shortly(backend, "ZW001"))

    result = await pair_device(backend, {"protocol": "zwave", "action": "exclude"})

    assert backend.discover_calls == [(None, "zwave", "exclude")]
    assert result["status"] == "exclusion_committed"
    assert result["removed_devices"] == [{"address": "ZW001", "name": "Front Door Lock"}]


@pytest.mark.asyncio
async def test_exclude_rejects_an_unknown_device_address_for_zwave_when_one_is_given():
    backend = FakeBackend()
    result = await pair_device(
        backend, {"protocol": "zwave", "action": "exclude", "device_address": "nope"}
    )
    assert "error" in result
    assert backend.discover_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["zigbee", "matter"])
async def test_exclude_requires_device_address_for_zigbee_and_matter(protocol):
    # Unlike zwave, zigbee/matter target one specific address directly --
    # there's no physical activation window to identify the device instead.
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": protocol, "action": "exclude"})
    assert "error" in result
    assert backend.remove_device_calls == []


@pytest.mark.asyncio
async def test_exclude_errors_if_the_node_removed_event_never_arrives():
    backend = FakeBackend()
    backend.nodes["ZW001"] = SimpleNamespace(name="Front Door Lock")

    result = await pair_device(
        backend, {"protocol": "zwave", "action": "exclude", "device_address": "ZW001"}
    )

    assert "error" in result


@pytest.mark.asyncio
async def test_exclude_removes_directly_for_matter():
    # matter has no activation-window concept -- eisy-ui only ever exposes
    # a direct per-address remove endpoint (see remove_device()/
    # _remove_zmatter_device) -- one call is the whole operation. No
    # documented "disabled instead of removed" fallback for matter, so a
    # plain wait for the address to disappear is enough (unlike zigbee).
    backend = FakeBackend()
    backend.nodes["MT001"] = SimpleNamespace(name="Front Door Sensor")

    result = await pair_device(
        backend, {"protocol": "matter", "action": "exclude", "device_address": "MT001"}
    )

    assert result["status"] == "exclusion_committed"
    assert result["removed_devices"] == [{"address": "MT001", "name": "Front Door Sensor"}]
    assert backend.remove_device_calls == [("MT001", "matter")]
    assert "MT001" not in backend.nodes
    assert backend.discover_calls == []  # the windowed zwave path is never reached


@pytest.mark.asyncio
async def test_exclude_removes_directly_for_zigbee_when_nr_arrives():
    backend = FakeBackend()
    backend.nodes["ZB001"] = SimpleNamespace(name="Front Door Sensor")
    asyncio.create_task(_fire_node_removed_event_shortly(backend, "ZB001"))

    result = await pair_device(
        backend, {"protocol": "zigbee", "action": "exclude", "device_address": "ZB001"}
    )

    assert result["status"] == "exclusion_committed"
    assert result["removed_devices"] == [{"address": "ZB001", "name": "Front Door Sensor"}]
    assert backend.remove_device_calls == [("ZB001", "zigbee")]
    assert backend.discover_calls == []


@pytest.mark.asyncio
async def test_exclude_errors_for_zigbee_when_the_hub_only_disables_the_node():
    # Zigbee removal can fail silently at the network layer -- the hub
    # disables the node locally instead of actually removing it, firing
    # _3/"EN" with eventInfo {"enabled": "false"} rather than _3/"NR".
    backend = FakeBackend()
    backend.nodes["ZB001"] = SimpleNamespace(name="Front Door Sensor")

    async def fire_disabled_event_shortly():
        await asyncio.sleep(0.02)
        backend._dispatch_event_listeners("_3", "EN", "ZB001", {"enabled": "false"})

    asyncio.create_task(fire_disabled_event_shortly())

    result = await pair_device(
        backend, {"protocol": "zigbee", "action": "exclude", "device_address": "ZB001"}
    )

    assert "error" in result
    assert "manually" in result["error"]
    assert result["device_address"] == "ZB001"


@pytest.mark.asyncio
async def test_exclude_ignores_an_unrelated_en_event_for_zigbee():
    # enabled=true (re-enabled, not the failure signal) and any other
    # action on _3 must be discarded rather than mistaken for an outcome --
    # only NR or EN-with-enabled=false end the wait.
    backend = FakeBackend()
    backend.nodes["ZB001"] = SimpleNamespace(name="Front Door Sensor")

    async def fire_events_shortly():
        await asyncio.sleep(0.01)
        backend._dispatch_event_listeners("_3", "EN", "ZB001", {"enabled": "true"})
        await asyncio.sleep(0.01)
        backend._dispatch_event_listeners("_3", "NR", "ZB001", {})

    asyncio.create_task(fire_events_shortly())

    result = await pair_device(
        backend, {"protocol": "zigbee", "action": "exclude", "device_address": "ZB001"}
    )

    assert result["status"] == "exclusion_committed"


@pytest.mark.asyncio
async def test_exclude_errors_for_zigbee_when_neither_event_arrives():
    backend = FakeBackend()
    backend.nodes["ZB001"] = SimpleNamespace(name="Front Door Sensor")

    result = await pair_device(
        backend, {"protocol": "zigbee", "action": "exclude", "device_address": "ZB001"}
    )

    assert "error" in result
    assert "manually" not in result["error"]  # a plain timeout, not the disabled-node message


@pytest.mark.asyncio
async def test_zigbee_removal_error_surfaces_from_remove_device():
    backend = FakeBackend()
    backend.nodes["ZB001"] = SimpleNamespace(name="Front Door Sensor")
    backend.remove_device_error = NuCoreError("remove_device is not supported for protocol 'zigbee'")

    result = await pair_device(
        backend, {"protocol": "zigbee", "action": "exclude", "device_address": "ZB001"}
    )

    assert "error" in result
    assert "not supported" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["include", "exclude"])
async def test_legacy_zwave_returns_a_clear_error_instead_of_pairing(action):
    backend = FakeBackend()
    backend.discover_error = NuCoreError(
        "Legacy Z-Wave pairing is not supported -- use the ISY administrative console instead."
    )
    args = {"protocol": "zwave", "action": action}
    if action == "exclude":
        backend.nodes["ZW001"] = SimpleNamespace(name="Front Door Lock")
        args["device_address"] = "ZW001"
    result = await pair_device(backend, args)
    assert "Legacy Z-Wave" in result["error"]
    assert backend.discover_calls == []


# ---------------------------------------------------------------------------
# Structural rejection -- invalid protocol/action pairing, independent of
# implementation status. Locks in the protocol/action validity table.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["zwave", "zigbee", "matter"])
async def test_add_by_address_is_structurally_invalid_for_non_insteon(protocol):
    # x10 is excluded here -- add_by_address IS a valid action for x10 (see
    # _PROTOCOL_ACTIONS), it's just not implemented yet; that's covered by
    # test_x10_add_by_address_is_not_yet_supported instead.
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": protocol, "action": "add_by_address"})
    assert "error" in result
    assert "not valid for protocol" in result["error"]


@pytest.mark.asyncio
async def test_include_is_structurally_invalid_for_x10():
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": "x10", "action": "include"})
    assert "error" in result
    assert "not valid for protocol" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["insteon", "x10"])
async def test_exclude_is_structurally_invalid_for_insteon_and_x10(protocol):
    # Neither insteon nor x10 model a distinct hardware "exclude" mode in
    # this codebase -- only zwave/zigbee/matter get this action.
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": protocol, "action": "exclude"})
    assert "error" in result
    assert "not valid for protocol" in result["error"]


@pytest.mark.asyncio
async def test_unknown_protocol_is_rejected():
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": "bluetooth", "action": "add_by_address"})
    assert "error" in result


@pytest.mark.asyncio
async def test_unknown_action_is_rejected_for_a_known_protocol():
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": "insteon", "action": "teleport"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Dispatch-level: callable standalone and mid-Plan-session
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_callable_standalone_via_dispatch():
    backend = FakeBackend()
    result = await execute_tool(
        "pair_device",
        {"protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1"},
        nucore_interface=backend,
    )
    assert result["status"] == "added"


@pytest.mark.asyncio
async def test_callable_while_a_plan_session_is_running():
    backend = FakeBackend()
    await execute_tool(
        "start_plan", {"plan_type": "new_installation"}, nucore_interface=backend, session_id="s1"
    )

    result = await execute_tool(
        "pair_device",
        {"protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1"},
        nucore_interface=backend,
        session_id="s1",
    )

    assert result["status"] == "added"

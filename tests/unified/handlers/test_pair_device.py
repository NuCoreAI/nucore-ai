"""``pair_device`` -- standalone global tool, not gated behind a Plan
session. Covers: the protocol/action validity table (structural rejection
vs. not-yet-supported), each of insteon's 3 real actions, the
zwave/zigbee/matter inclusion+exclusion actions, the Legacy-Z-Wave error
carve-out, and that the tool stays callable via dispatch.execute_tool while
a plan session is running for the owning session_id (confirms dispatch.py's
_PLAN_ALWAYS_IMMEDIATE_TOOLS/_SESSION_SCOPED_TOOLS split doesn't crash on an
unexpected session_id kwarg).

Also covers the post-pairing refresh-and-verify polling in pair_device.py's
_refresh_until: add_device()/finish_device_discovery() are plain REST calls
that don't themselves update the local node list, so pair_device polls
_refresh_device_structure() until the newly-added device actually shows up
locally (add_by_address), or -- the mirror image -- until an already-known
device_address disappears (finish_exclusion), rather than assuming one
refresh call is enough. Also covers start_inclusion/finish_inclusion's
_inclusion_baselines: the "before" node-address snapshot is taken when
start_inclusion opens the pairing window, not when finish_inclusion is
later called, since a device activated mid-window can already be reflected
in nucore_interface.nodes (via the routine per-turn refresh) by the time
finish_inclusion runs.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nucore.nucore_error import NuCoreError
from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool
from unified.handlers import pair_device as pair_device_module
from unified.handlers.pair_device import normalize_address, pair_device


async def _instant_sleep(_seconds):
    """Replaces asyncio.sleep so a test whose condition never becomes true
    doesn't actually block for real seconds -- _refresh_until checks its
    condition on every poll regardless of whether _refresh_device_structure()
    itself reports a reload, so any test where nothing ever changes runs the
    full poll budget's worth of iterations, just without a real wait."""
    return None


@pytest.fixture(autouse=True)
def _no_real_sleeps(monkeypatch):
    monkeypatch.setattr(pair_device_module.asyncio, "sleep", _instant_sleep)


@pytest.fixture(autouse=True)
def _clean_inclusion_baselines():
    # _inclusion_baselines is module-level, shared-instance state (mirrors
    # the real hub, which can only run one inclusion window per protocol at
    # a time) -- clear it around every test so a start_inclusion left
    # dangling by one test (no matching finish_inclusion) can't leak into a
    # later, unrelated test that happens to use the same protocol.
    pair_device_module._inclusion_baselines.clear()
    yield
    pair_device_module._inclusion_baselines.clear()


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.add_device_calls: list[tuple] = []
        self.discover_calls: list[tuple] = []
        self.finish_calls: list[tuple] = []
        self.remove_device_calls: list[tuple] = []
        self.add_device_result: object = "1A 2B 3C 1"
        self.discover_result: bool = True
        self.finish_result: bool = True
        self.remove_device_result: bool = True
        self.discover_error: Exception | None = None
        self.finish_error: Exception | None = None
        self.remove_device_error: Exception | None = None
        self.refresh_calls = 0
        # add_device stages the address here, and the default
        # _refresh_device_structure below merges it into self.nodes on its
        # first call -- simulates a real hub reload finding what was just
        # added, without needing every existing test to script the refresh
        # itself. Tests that need to exercise the polling/retry/timeout
        # logic directly override backend._refresh_device_structure instead.
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
        if self.finish_error is not None:
            raise self.finish_error
        self.finish_calls.append((flag, protocol))
        return self.finish_result

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
        # (rather than only when something changed) keeps tests that don't
        # care about the refresh mechanics (e.g. finish_inclusion with
        # nothing staged) from blocking on _refresh_until's real sleep loop.
        self.refresh_calls += 1
        for address, name in self._pending_nodes.items():
            self.nodes[address] = SimpleNamespace(name=name)
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
    def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    def group_scene_update_link(self, *a, **kw): raise NotImplementedError
    def group_scene_get_node_roles(self, *a, **kw): raise NotImplementedError
    def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError


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
# Insteon -- the only implemented protocol
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
async def test_start_inclusion_succeeds():
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": "insteon", "action": "start_inclusion"})
    assert result["status"] == "inclusion_started"
    assert backend.discover_calls == [(None, "insteon", "include")]


@pytest.mark.asyncio
async def test_finish_inclusion_succeeds():
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": "insteon", "action": "finish_inclusion"})
    assert result["status"] == "inclusion_committed"
    assert backend.finish_calls == [(1, "insteon")]


@pytest.mark.asyncio
async def test_finish_inclusion_passes_through_flag():
    backend = FakeBackend()
    await pair_device(backend, {"protocol": "insteon", "action": "finish_inclusion", "flag": 3})
    assert backend.finish_calls == [(3, "insteon")]


# ---------------------------------------------------------------------------
# Post-pairing refresh-and-verify polling (_refresh_until)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_by_address_matches_the_hub_reported_address_case_insensitively():
    # The hub's own XML report can come back in a different case than
    # whatever the customer/model typed -- the presence check must not
    # treat that as "not found yet".
    backend = FakeBackend()

    async def reports_lowercase_address():
        backend.nodes["1a 2b 3c 1"] = SimpleNamespace(name="1a 2b 3c 1")
        return True

    backend._refresh_device_structure = reports_lowercase_address

    result = await pair_device(backend, {
        "protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })

    assert result["status"] == "added"


@pytest.mark.asyncio
async def test_add_by_address_waits_for_refresh_before_succeeding():
    backend = FakeBackend()
    calls = {"n": 0}

    async def flaky_refresh():
        calls["n"] += 1
        if calls["n"] < 3:
            return False
        backend.nodes["1A 2B 3C 1"] = SimpleNamespace(name="1A 2B 3C 1")
        return True

    backend._refresh_device_structure = flaky_refresh

    result = await pair_device(backend, {
        "protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })

    assert result["status"] == "added"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_add_by_address_errors_if_never_refreshed():
    backend = FakeBackend()

    async def never_refreshes():
        return False

    backend._refresh_device_structure = never_refreshes

    result = await pair_device(backend, {
        "protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })

    assert "error" in result


@pytest.mark.asyncio
async def test_add_by_address_errors_if_refreshed_but_device_still_missing():
    backend = FakeBackend()

    async def refreshes_but_finds_nothing():
        # Reload "succeeds" each time, but the target address never actually
        # appears in backend.nodes -- exhausts _REFRESH_MAX_ATTEMPTS.
        return True

    backend._refresh_device_structure = refreshes_but_finds_nothing

    result = await pair_device(backend, {
        "protocol": "insteon", "action": "add_by_address", "device_address": "1A 2B 3C 1",
    })

    assert "error" in result


@pytest.mark.asyncio
async def test_finish_inclusion_returns_newly_discovered_addresses():
    backend = FakeBackend()
    await pair_device(backend, {"protocol": "insteon", "action": "start_inclusion"})

    async def discovers_two_devices():
        backend.nodes["11 11 11 1"] = SimpleNamespace(name="11 11 11 1")
        backend.nodes["22 22 22 1"] = SimpleNamespace(name="22 22 22 1")
        return True

    backend._refresh_device_structure = discovers_two_devices

    result = await pair_device(backend, {"protocol": "insteon", "action": "finish_inclusion"})

    assert result["status"] == "inclusion_committed"
    assert {d["address"] for d in result["new_devices"]} == {"11 11 11 1", "22 22 22 1"}


@pytest.mark.asyncio
async def test_finish_inclusion_reports_nothing_new_without_erroring():
    backend = FakeBackend()
    await pair_device(backend, {"protocol": "insteon", "action": "start_inclusion"})

    async def refreshes_but_nothing_new():
        return True

    backend._refresh_device_structure = refreshes_but_nothing_new

    result = await pair_device(backend, {"protocol": "insteon", "action": "finish_inclusion"})

    assert result["status"] == "inclusion_committed"
    assert result["new_devices"] == []
    assert "error" not in result


@pytest.mark.asyncio
async def test_finish_inclusion_finds_a_device_that_appeared_before_finish_was_called():
    # Regression test: the customer activates the device mid-window, and the
    # routine per-turn refresh (build_system_prompt -> _refresh_device_structure)
    # can pick it up well before the customer says "done" and finish_inclusion
    # actually runs. If "before" were snapshotted at finish_inclusion time
    # (the old bug), this device would already be in that snapshot and never
    # get reported.
    backend = FakeBackend()
    await pair_device(backend, {"protocol": "zwave", "action": "start_inclusion"})

    # Simulates the routine per-turn refresh landing the new node before
    # finish_inclusion is ever called.
    backend.nodes["ZW099"] = SimpleNamespace(name="New Sensor")

    result = await pair_device(backend, {"protocol": "zwave", "action": "finish_inclusion"})

    assert result["status"] == "inclusion_committed"
    assert [d["address"] for d in result["new_devices"]] == ["ZW099"]


@pytest.mark.asyncio
async def test_start_inclusion_baselines_are_independent_per_protocol():
    # Starting zigbee's inclusion window must not clobber zwave's
    # already-recorded baseline (each protocol gets its own dict entry).
    backend = FakeBackend()
    backend.nodes["EXISTING"] = SimpleNamespace(name="Existing Device")

    await pair_device(backend, {"protocol": "zwave", "action": "start_inclusion"})
    await pair_device(backend, {"protocol": "zigbee", "action": "start_inclusion"})
    # A zigbee device is committed (and its window closed) before the zwave
    # device shows up, so there's no ambiguity about which window it belongs
    # to when zwave's own finish_inclusion runs afterward.
    backend.nodes["ZB001"] = SimpleNamespace(name="Zigbee Device")
    zigbee_result = await pair_device(backend, {"protocol": "zigbee", "action": "finish_inclusion"})
    backend.nodes["ZW001"] = SimpleNamespace(name="Zwave Device")
    zwave_result = await pair_device(backend, {"protocol": "zwave", "action": "finish_inclusion"})

    assert [d["address"] for d in zigbee_result["new_devices"]] == ["ZB001"]
    assert [d["address"] for d in zwave_result["new_devices"]] == ["ZB001", "ZW001"]


@pytest.mark.asyncio
async def test_finish_inclusion_baseline_is_not_reused_after_being_consumed():
    backend = FakeBackend()
    await pair_device(backend, {"protocol": "insteon", "action": "start_inclusion"})
    backend.nodes["11 11 11 1"] = SimpleNamespace(name="First Device")
    first = await pair_device(backend, {"protocol": "insteon", "action": "finish_inclusion"})
    assert [d["address"] for d in first["new_devices"]] == ["11 11 11 1"]

    # No new start_inclusion call -- the baseline was already popped, so this
    # falls back to a same-call snapshot and must not re-report the device
    # that was already committed above.
    second = await pair_device(backend, {"protocol": "insteon", "action": "finish_inclusion"})
    assert second["new_devices"] == []


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
# Z-Wave/Zigbee/Matter -- inclusion (add) and exclusion (remove)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["zwave", "zigbee", "matter"])
async def test_start_inclusion_succeeds_for_zmatter_protocols(protocol):
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": protocol, "action": "start_inclusion"})
    assert result["status"] == "inclusion_started"
    assert backend.discover_calls == [(None, protocol, "include")]


@pytest.mark.asyncio
async def test_start_exclusion_opens_an_activation_window_for_zwave():
    # zwave is the only protocol with a real hardware exclude-mode window --
    # see zigbee/matter's direct-removal tests below.
    backend = FakeBackend()
    backend.nodes["ZW001"] = SimpleNamespace(name="Front Door Lock")
    result = await pair_device(
        backend, {"protocol": "zwave", "action": "start_exclusion", "device_address": "ZW001"}
    )
    assert result["status"] == "exclusion_started"
    assert result["device_address"] == "ZW001"
    assert backend.discover_calls == [(None, "zwave", "exclude")]
    assert backend.remove_device_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["zigbee", "matter"])
@pytest.mark.parametrize("action", ["start_exclusion", "finish_exclusion"])
async def test_exclusion_removes_directly_for_zigbee_and_matter(protocol, action):
    # Neither zigbee nor matter has an activation-window concept -- eisy-ui
    # only ever exposes a direct per-address remove endpoint for these two
    # (see remove_device()/_remove_zmatter_device) -- so either action name
    # performs the whole removal by itself, in one call.
    backend = FakeBackend()
    backend.nodes["ZB001"] = SimpleNamespace(name="Front Door Sensor")

    result = await pair_device(
        backend, {"protocol": protocol, "action": action, "device_address": "ZB001"}
    )

    assert result["status"] == "exclusion_committed"
    assert result["removed_devices"] == [{"address": "ZB001", "name": "Front Door Sensor"}]
    assert backend.remove_device_calls == [("ZB001", protocol)]
    assert "ZB001" not in backend.nodes
    # Neither the zwave-only activation-window calls nor the other
    # exclusion action should ever be reached for these protocols.
    assert backend.discover_calls == []
    assert backend.finish_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["zigbee", "matter"])
async def test_calling_both_exclusion_actions_for_zigbee_matter_is_harmless(protocol):
    # A model that (out of habit, matching zwave's two-step shape) calls
    # start_exclusion then finish_exclusion for zigbee/matter must not error
    # on the second call just because the device is already gone.
    backend = FakeBackend()
    backend.nodes["ZB001"] = SimpleNamespace(name="Front Door Sensor")

    first = await pair_device(
        backend, {"protocol": protocol, "action": "start_exclusion", "device_address": "ZB001"}
    )
    second = await pair_device(
        backend, {"protocol": protocol, "action": "finish_exclusion", "device_address": "ZB001"}
    )

    assert first["status"] == "exclusion_committed"
    assert second["status"] == "exclusion_committed"
    # The device is already gone by the second call, so its name is no
    # longer available -- _remove_zmatter_device falls back to the address,
    # same convention as add_by_address's own not-found fallback.
    assert second["removed_devices"] == [{"address": "ZB001", "name": "ZB001"}]
    assert len(backend.remove_device_calls) == 2


@pytest.mark.asyncio
async def test_zigbee_removal_error_surfaces_from_remove_device():
    backend = FakeBackend()
    backend.nodes["ZB001"] = SimpleNamespace(name="Front Door Sensor")
    backend.remove_device_error = NuCoreError("remove_device is not supported for protocol 'zigbee'")

    result = await pair_device(
        backend, {"protocol": "zigbee", "action": "start_exclusion", "device_address": "ZB001"}
    )

    assert "error" in result
    assert "not supported" in result["error"]


@pytest.mark.asyncio
async def test_start_exclusion_requires_device_address():
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": "zwave", "action": "start_exclusion"})
    assert "error" in result
    assert backend.discover_calls == []


@pytest.mark.asyncio
async def test_start_exclusion_rejects_an_unknown_device_address():
    backend = FakeBackend()
    result = await pair_device(
        backend, {"protocol": "zwave", "action": "start_exclusion", "device_address": "nope"}
    )
    assert "error" in result
    assert backend.discover_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["zwave", "zigbee", "matter"])
async def test_finish_inclusion_returns_newly_discovered_addresses_for_zmatter_protocols(protocol):
    backend = FakeBackend()
    await pair_device(backend, {"protocol": protocol, "action": "start_inclusion"})

    async def discovers_one_device():
        backend.nodes["ZW001"] = SimpleNamespace(name="ZW001")
        return True

    backend._refresh_device_structure = discovers_one_device

    result = await pair_device(backend, {"protocol": protocol, "action": "finish_inclusion"})

    assert result["status"] == "inclusion_committed"
    assert backend.finish_calls == [(1, protocol)]
    assert [d["address"] for d in result["new_devices"]] == ["ZW001"]


@pytest.mark.asyncio
async def test_finish_exclusion_commits_the_activation_window_for_zwave():
    backend = FakeBackend()
    backend.nodes["ZW001"] = SimpleNamespace(name="Front Door Lock")

    async def excludes_the_device():
        del backend.nodes["ZW001"]
        return True

    backend._refresh_device_structure = excludes_the_device

    result = await pair_device(
        backend, {"protocol": "zwave", "action": "finish_exclusion", "device_address": "ZW001"}
    )

    assert result["status"] == "exclusion_committed"
    assert backend.finish_calls == [(1, "zwave")]
    assert result["removed_devices"] == [{"address": "ZW001", "name": "Front Door Lock"}]
    assert "ZW001" not in backend.nodes
    assert backend.remove_device_calls == []


@pytest.mark.asyncio
async def test_finish_exclusion_requires_device_address():
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": "zwave", "action": "finish_exclusion"})
    assert "error" in result
    assert backend.finish_calls == []


@pytest.mark.asyncio
async def test_finish_exclusion_errors_if_the_address_never_disappears():
    backend = FakeBackend()
    backend.nodes["ZW001"] = SimpleNamespace(name="Front Door Lock")

    async def refreshes_but_device_still_there():
        return True

    backend._refresh_device_structure = refreshes_but_device_still_there

    result = await pair_device(
        backend, {"protocol": "zwave", "action": "finish_exclusion", "device_address": "ZW001"}
    )

    assert "error" in result
    assert "ZW001" in backend.nodes


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["start_inclusion", "start_exclusion"])
async def test_legacy_zwave_returns_a_clear_error_instead_of_pairing(action):
    backend = FakeBackend()
    backend.discover_error = NuCoreError(
        "Legacy Z-Wave pairing is not supported -- use the ISY administrative console instead."
    )
    args = {"protocol": "zwave", "action": action}
    if action == "start_exclusion":
        backend.nodes["ZW001"] = SimpleNamespace(name="Front Door Lock")
        args["device_address"] = "ZW001"
    result = await pair_device(backend, args)
    assert "Legacy Z-Wave" in result["error"]
    assert backend.discover_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["finish_inclusion", "finish_exclusion"])
async def test_legacy_zwave_finish_returns_a_clear_error_instead_of_pairing(action):
    backend = FakeBackend()
    backend.finish_error = NuCoreError(
        "Legacy Z-Wave pairing is not supported -- use the ISY administrative console instead."
    )
    args = {"protocol": "zwave", "action": action}
    if action == "finish_exclusion":
        backend.nodes["ZW001"] = SimpleNamespace(name="Front Door Lock")
        args["device_address"] = "ZW001"
    result = await pair_device(backend, args)
    assert "Legacy Z-Wave" in result["error"]
    assert backend.finish_calls == []


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
async def test_start_inclusion_is_structurally_invalid_for_x10():
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": "x10", "action": "start_inclusion"})
    assert "error" in result
    assert "not valid for protocol" in result["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["insteon", "x10"])
@pytest.mark.parametrize("action", ["start_exclusion", "finish_exclusion"])
async def test_exclusion_actions_are_structurally_invalid_for_insteon_and_x10(protocol, action):
    # Neither insteon nor x10 model a distinct hardware "exclude" mode in
    # this codebase -- only zwave/zigbee/matter get these actions.
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": protocol, "action": action})
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

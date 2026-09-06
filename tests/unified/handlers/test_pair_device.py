"""``pair_device`` -- standalone global tool, not gated behind a Plan
session. Covers: the protocol/action validity table (structural rejection
vs. not-yet-supported), each of insteon's 3 real actions, and that the tool
stays callable via dispatch.execute_tool while a plan session is running
for the owning session_id (confirms dispatch.py's _PLAN_EXEMPT_TOOLS/
_SESSION_SCOPED_TOOLS split doesn't crash on an unexpected session_id
kwarg).
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool
from unified.handlers.pair_device import pair_device


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.add_device_calls: list[tuple] = []
        self.discover_calls: list[tuple] = []
        self.finish_calls: list[tuple] = []
        self.add_device_result: object = "1A 2B 3C 1"
        self.discover_result: bool = True
        self.finish_result: bool = True

    async def add_device(self, device_address, name=None, device_type=None, **kwargs):
        self.add_device_calls.append((device_address, name, device_type))
        return self.add_device_result

    async def discover_devices(self, device_type=None, **kwargs):
        self.discover_calls.append((device_type,))
        return self.discover_result

    async def finish_device_discovery(self, flag=1, **kwargs):
        self.finish_calls.append((flag,))
        return self.finish_result

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
    assert backend.discover_calls == [(None,)]


@pytest.mark.asyncio
async def test_finish_inclusion_succeeds():
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": "insteon", "action": "finish_inclusion"})
    assert result["status"] == "inclusion_committed"
    assert backend.finish_calls == [(1,)]


@pytest.mark.asyncio
async def test_finish_inclusion_passes_through_flag():
    backend = FakeBackend()
    await pair_device(backend, {"protocol": "insteon", "action": "finish_inclusion", "flag": 3})
    assert backend.finish_calls == [(3,)]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["zwave", "zigbee", "matter"])
@pytest.mark.parametrize("action", ["start_inclusion", "finish_inclusion"])
async def test_unimplemented_protocols_are_not_yet_supported(protocol, action):
    backend = FakeBackend()
    result = await pair_device(backend, {"protocol": protocol, "action": action})
    assert "not yet supported" in result


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

"""End-to-end: start_plan/run_plan_step dispatched through execute_tool --
confirms the handler's string-params recovery, the lazy per-instance
PlanEngine attachment, the session-ownership gate, and that a running Plan
session still blocks Diagnostics tools (Plan's own blanket lock refuses
every other tool). This is one-directional now, not mutual: Diagnostics has
no session/blanket lock of its own any more, so a Diagnostics tool never
blocks Plan.
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool
from unified.handlers import plan


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.pairing_calls: list[str] = []

    async def run_diagnostic_step(self, step, **params): raise NotImplementedError

    async def add_device(self, device_address, **kwargs):
        self.pairing_calls.append(device_address)
        return {"status": "added"}

    async def discover_devices(self): raise NotImplementedError
    async def finish_device_discovery(self): raise NotImplementedError

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


@pytest.mark.asyncio
async def test_start_plan_requires_plan_type():
    backend = FakeBackend()
    result = await execute_tool("start_plan", {}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_start_plan_returns_not_implemented_for_a_stub_type():
    backend = FakeBackend()
    result = await execute_tool("start_plan", {"plan_type": "holidays"}, nucore_interface=backend)
    assert result["status"] == "not_implemented"


@pytest.mark.asyncio
async def test_start_plan_opens_new_installation():
    backend = FakeBackend()
    result = await execute_tool(
        "start_plan", {"plan_type": "new_installation"}, nucore_interface=backend, session_id="s1"
    )
    assert result["status"] == "in_progress"
    assert "pair_device" in result["available_tools"]


@pytest.mark.asyncio
async def test_get_engine_attaches_and_reuses_the_same_instance_per_backend():
    backend = FakeBackend()
    engine1 = plan._get_engine(backend)
    engine2 = plan._get_engine(backend)
    assert engine1 is engine2
    assert backend._plan_engine is engine1


@pytest.mark.asyncio
async def test_run_plan_step_requires_step():
    backend = FakeBackend()
    await execute_tool("start_plan", {"plan_type": "new_installation"}, nucore_interface=backend, session_id="s1")
    result = await execute_tool("run_plan_step", {}, nucore_interface=backend, session_id="s1")
    assert "error" in result


@pytest.mark.asyncio
async def test_run_plan_step_recovers_stringified_json_params():
    backend = FakeBackend()
    await execute_tool("start_plan", {"plan_type": "new_installation"}, nucore_interface=backend, session_id="s1")

    result = await execute_tool(
        "run_plan_step",
        {"step": "pair_device", "params": '{"protocol": "insteon", "device_address": "1A 2B 3C 1"}'},
        nucore_interface=backend,
        session_id="s1",
    )

    assert backend.pairing_calls == ["1A 2B 3C 1"]
    assert result["step"] == "pair_device"


@pytest.mark.asyncio
async def test_run_plan_step_rejects_a_non_json_string_params():
    backend = FakeBackend()
    await execute_tool("start_plan", {"plan_type": "new_installation"}, nucore_interface=backend, session_id="s1")

    result = await execute_tool(
        "run_plan_step", {"step": "review_plan", "params": "not json"}, nucore_interface=backend, session_id="s1"
    )

    assert "error" in result


@pytest.mark.asyncio
async def test_other_tools_are_blocked_while_a_plan_is_running():
    backend = FakeBackend()
    await execute_tool("start_plan", {"plan_type": "new_installation"}, nucore_interface=backend, session_id="s1")

    result = await execute_tool(
        "get_property", {"device_id": "n001", "property": "ST"}, nucore_interface=backend, session_id="s1"
    )

    assert "error" in result


@pytest.mark.asyncio
async def test_a_different_session_is_refused_even_for_the_plan_tools():
    backend = FakeBackend()
    await execute_tool("start_plan", {"plan_type": "new_installation"}, nucore_interface=backend, session_id="s1")

    result = await execute_tool(
        "run_plan_step", {"step": "review_plan"}, nucore_interface=backend, session_id="s2"
    )

    assert "error" in result


@pytest.mark.asyncio
async def test_a_running_plan_blocks_a_diagnostics_tool():
    # Plan's own blanket lock refuses every other tool, including
    # Diagnostics' -- Diagnostics has no equivalent lock of its own (no
    # session left to gate), so this is one-directional.
    backend = FakeBackend()
    await execute_tool("start_plan", {"plan_type": "new_installation"}, nucore_interface=backend, session_id="s1")

    result = await execute_tool(
        "run_diagnostic_step", {"step": "get_full_system_config"}, nucore_interface=backend, session_id="s1"
    )

    assert "error" in result


@pytest.mark.asyncio
async def test_stop_closes_the_session():
    backend = FakeBackend()
    await execute_tool("start_plan", {"plan_type": "new_installation"}, nucore_interface=backend, session_id="s1")

    result = await execute_tool("run_plan_step", {"step": "stop"}, nucore_interface=backend, session_id="s1")

    assert result == {"status": "stopped"}
    assert plan.get_running_plan(backend) is None

    # session really is closed -- a normal tool reaches its real handler now,
    # instead of being refused by the plan-in-progress gate.
    followup = await execute_tool(
        "get_property", {"device_id": "n001", "property": "ST"}, nucore_interface=backend, session_id="s1"
    )
    assert "plan session is currently in progress" not in followup.get("error", "")

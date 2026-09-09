"""End-to-end: start_plan/run_plan_step dispatched through execute_tool --
confirms the handler's string-params recovery, the lazy per-instance
PlanEngine attachment, the session-ownership gate, and the three-way tool
classification while a plan is running: a stageable tool (e.g.
create_or_update_routine) auto-stages instead of executing, a read-only
tool (e.g. get_device_detail) stays live, and everything else (e.g.
Diagnostics tools) is still refused outright.
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool
from unified.handlers import plan


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")

    async def run_diagnostic_step(self, step, **params): raise NotImplementedError

    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self): raise NotImplementedError
    async def finish_device_discovery(self): raise NotImplementedError
    async def remove_device(self, device_address, protocol=None, **kwargs): raise NotImplementedError

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
    assert "apply_plan" in result["available_tools"]


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
        {"step": "conclude", "params": '{"summary": "All done"}'},
        nucore_interface=backend,
        session_id="s1",
    )

    assert result == {"status": "completed", "summary": "All done"}


@pytest.mark.asyncio
async def test_run_plan_step_rejects_a_non_json_string_params():
    backend = FakeBackend()
    await execute_tool("start_plan", {"plan_type": "new_installation"}, nucore_interface=backend, session_id="s1")

    result = await execute_tool(
        "run_plan_step", {"step": "review_plan", "params": "not json"}, nucore_interface=backend, session_id="s1"
    )

    assert "error" in result


@pytest.mark.asyncio
async def test_stageable_tool_auto_stages_instead_of_executing():
    backend = FakeBackend()
    await execute_tool("start_plan", {"plan_type": "new_installation"}, nucore_interface=backend, session_id="s1")

    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Sunset Lights", "code": "pass"},
        nucore_interface=backend,
        session_id="s1",
    )

    # Staged, not actually run -- if it had reached the real handler, FakeBackend's
    # NotImplementedError stubs would have surfaced as an "error" result instead.
    assert result["status"] == "staged"
    assert "error" not in result


@pytest.mark.asyncio
async def test_read_only_tool_stays_live_while_a_plan_is_running():
    backend = FakeBackend()
    await execute_tool("start_plan", {"plan_type": "new_installation"}, nucore_interface=backend, session_id="s1")

    result = await execute_tool(
        "get_property", {"device_id": "n001", "property": "ST"}, nucore_interface=backend, session_id="s1"
    )

    # Reached the real handler (and failed for an unrelated FakeBackend-stub reason,
    # not because a plan is running).
    assert "isn't available while a plan session is in progress" not in result.get("error", "")


@pytest.mark.asyncio
async def test_unclassified_tool_is_still_blocked_while_a_plan_is_running():
    backend = FakeBackend()
    await execute_tool("start_plan", {"plan_type": "new_installation"}, nucore_interface=backend, session_id="s1")

    result = await execute_tool(
        "run_shell_command", {"command": "echo hi"}, nucore_interface=backend, session_id="s1"
    )

    assert "isn't available while a plan session is in progress" in result.get("error", "")


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
    # run_diagnostic_step is in none of the staged/always-immediate/read-only
    # sets, so it's still refused outright while a plan is running --
    # Diagnostics has no equivalent lock of its own (no session left to
    # gate), so this is one-directional.
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

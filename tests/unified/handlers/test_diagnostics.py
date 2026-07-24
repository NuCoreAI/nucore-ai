"""End-to-end: start_diagnostics/run_diagnostic_step dispatched through
execute_tool -- confirms the thin pass-through to
NuCoreInterface.start_diagnostics()/run_diagnostic_step().
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.start_calls: list[tuple] = []
        self.start_result = {"status": "in_progress", "instruction": "...", "available_tools": []}
        self.step_calls: list[tuple] = []
        self.step_result = {"step": "get_full_system_config", "result": "ok"}
        self.running_diagnostic = None

    async def start_diagnostics(self, **kwargs):
        self.start_calls.append(kwargs)
        return self.start_result

    async def run_diagnostic_step(self, step, **params):
        self.step_calls.append((step, params))
        return self.step_result

    def get_running_diagnostic(self):
        return self.running_diagnostic

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
async def test_start_diagnostics_returns_backend_result():
    backend = FakeBackend()
    result = await execute_tool("start_diagnostics", {}, nucore_interface=backend)
    assert result == backend.start_result
    assert backend.start_calls == [{}]


@pytest.mark.asyncio
async def test_start_diagnostics_passes_candidate_devices_and_routines_through():
    backend = FakeBackend()
    candidate_devices = [{"device_id": "n001", "score": 0.9}]
    candidate_routines = [{"routine_id": "r001", "score": 0.8}]

    await execute_tool(
        "start_diagnostics",
        {"candidate_devices": candidate_devices, "candidate_routines": candidate_routines},
        nucore_interface=backend,
    )

    assert backend.start_calls == [{"candidate_devices": candidate_devices, "candidate_routines": candidate_routines}]


@pytest.mark.asyncio
async def test_start_diagnostics_omits_candidate_kwargs_when_absent():
    backend = FakeBackend()
    await execute_tool("start_diagnostics", {}, nucore_interface=backend)
    assert backend.start_calls == [{}]


@pytest.mark.asyncio
async def test_other_tools_are_blocked_while_a_diagnostic_is_running():
    backend = FakeBackend()
    backend.running_diagnostic = {"status": "in_progress", "elapsed_s": 12}

    result = await execute_tool("get_property", {"device_id": "n001", "property": "ST"}, nucore_interface=backend)

    assert "error" in result


@pytest.mark.asyncio
async def test_start_diagnostics_and_run_diagnostic_step_stay_exempt_from_the_lock():
    backend = FakeBackend()
    backend.running_diagnostic = {"status": "in_progress", "elapsed_s": 12}

    start_result = await execute_tool("start_diagnostics", {}, nucore_interface=backend)
    step_result = await execute_tool("run_diagnostic_step", {"step": "conclude"}, nucore_interface=backend)

    assert start_result == backend.start_result
    assert backend.start_calls == [{}]
    assert step_result == backend.step_result
    assert backend.step_calls == [("conclude", {})]


@pytest.mark.asyncio
async def test_run_diagnostic_step_passes_step_and_params_through():
    backend = FakeBackend()

    result = await execute_tool(
        "run_diagnostic_step", {"step": "check_device_links", "params": {"device_id": "n001"}}, nucore_interface=backend
    )

    assert result == backend.step_result
    assert backend.step_calls == [("check_device_links", {"device_id": "n001"})]


@pytest.mark.asyncio
async def test_run_diagnostic_step_defaults_params_to_empty_dict():
    backend = FakeBackend()

    await execute_tool("run_diagnostic_step", {"step": "get_full_system_config"}, nucore_interface=backend)

    assert backend.step_calls == [("get_full_system_config", {})]


@pytest.mark.asyncio
async def test_run_diagnostic_step_requires_step():
    backend = FakeBackend()

    result = await execute_tool("run_diagnostic_step", {}, nucore_interface=backend)

    assert "error" in result
    assert backend.step_calls == []


@pytest.mark.asyncio
async def test_other_tools_proceed_normally_when_nothing_is_running():
    backend = FakeBackend()
    backend.running_diagnostic = None

    result = await execute_tool("get_property", {"device_id": "n001", "property": "ST"}, nucore_interface=backend)

    # Reached the real handler (no devices loaded -> device-not-found), not
    # blocked by the diagnostics lock.
    assert result == {"error": "no device found with id 'n001'; check DEVICE DATABASE"}

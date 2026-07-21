"""End-to-end: list_diagnostics/run_diagnostics dispatched through
execute_tool -- confirms the thin pass-through to
NuCoreInterface.get_diagnostics_map()/run_diagnostics().
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.map_result = [{"function": "G_PLM_INFO", "description": "...", "long_running": False}]
        self.run_calls: list[tuple] = []
        self.run_result = {"function": "G_PLM_INFO", "status": "completed", "result": "ok"}
        self.running_diagnostic = None

    def get_diagnostics_map(self):
        return self.map_result

    async def run_diagnostics(self, function, **kwargs):
        self.run_calls.append((function, kwargs))
        return self.run_result

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
async def test_list_diagnostics_returns_backend_map():
    backend = FakeBackend()
    result = await execute_tool("list_diagnostics", {}, nucore_interface=backend)
    assert result == {"diagnostics": backend.map_result}


@pytest.mark.asyncio
async def test_run_diagnostics_passes_function_through():
    backend = FakeBackend()
    result = await execute_tool("run_diagnostics", {"function": "G_PLM_INFO"}, nucore_interface=backend)
    assert result == backend.run_result
    assert backend.run_calls == [("G_PLM_INFO", {})]


@pytest.mark.asyncio
async def test_run_diagnostics_passes_candidate_devices_and_routines_through():
    backend = FakeBackend()
    candidate_devices = [{"device_id": "n001", "score": 0.9}]
    candidate_routines = [{"routine_id": "r001", "score": 0.8}]

    await execute_tool(
        "run_diagnostics",
        {"function": "G_PLM_INFO", "candidate_devices": candidate_devices, "candidate_routines": candidate_routines},
        nucore_interface=backend,
    )

    assert backend.run_calls == [
        ("G_PLM_INFO", {"candidate_devices": candidate_devices, "candidate_routines": candidate_routines})
    ]


@pytest.mark.asyncio
async def test_run_diagnostics_omits_candidate_kwargs_when_absent():
    backend = FakeBackend()
    await execute_tool("run_diagnostics", {"function": "G_PLM_INFO"}, nucore_interface=backend)
    assert backend.run_calls == [("G_PLM_INFO", {})]


@pytest.mark.asyncio
async def test_run_diagnostics_requires_function():
    backend = FakeBackend()
    result = await execute_tool("run_diagnostics", {}, nucore_interface=backend)
    assert "error" in result
    assert backend.run_calls == []


@pytest.mark.asyncio
async def test_other_tools_are_blocked_while_a_diagnostic_is_running():
    backend = FakeBackend()
    backend.running_diagnostic = {"diagnostics": "G_ALL_PLM_LINKS", "status": "running", "elapsed_s": 12}

    result = await execute_tool("get_property", {"device_id": "n001", "property": "ST"}, nucore_interface=backend)

    assert "error" in result
    assert "G_ALL_PLM_LINKS" in result["error"]


@pytest.mark.asyncio
async def test_run_diagnostics_and_list_diagnostics_stay_exempt_from_the_lock():
    backend = FakeBackend()
    backend.running_diagnostic = {"diagnostics": "G_ALL_PLM_LINKS", "status": "running", "elapsed_s": 12}

    list_result = await execute_tool("list_diagnostics", {}, nucore_interface=backend)
    run_result = await execute_tool("run_diagnostics", {"function": "STOP"}, nucore_interface=backend)

    assert list_result == {"diagnostics": backend.map_result}
    assert run_result == backend.run_result
    assert backend.run_calls == [("STOP", {})]


@pytest.mark.asyncio
async def test_other_tools_proceed_normally_when_nothing_is_running():
    backend = FakeBackend()
    backend.running_diagnostic = None

    result = await execute_tool("get_property", {"device_id": "n001", "property": "ST"}, nucore_interface=backend)

    # Reached the real handler (no devices loaded -> device-not-found), not
    # blocked by the diagnostics lock.
    assert result == {"error": "no device found with id 'n001'; check DEVICE DATABASE"}

"""End-to-end: run_diagnostic_step/get_diagnostics_prompt dispatched through
execute_tool -- confirms the thin pass-through to
NuCoreInterface.run_diagnostic_step(), the stringified-params recovery, and
that there's no dispatch-level gating any more (no session, so nothing to
block other tools -- see tests/iox/test_run_diagnostics.py for the narrow
4-way PLM lock that lives inside IoXDiagnostics instead).
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool
from unified.handlers.diagnostics import _DIAGNOSTICS_PROMPT


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.step_calls: list[tuple] = []
        self.step_result = {"step": "get_full_system_config", "result": "ok"}

    async def run_diagnostic_step(self, step, **params):
        self.step_calls.append((step, params))
        return self.step_result

    async def add_device(self, device_address, **kwargs): raise NotImplementedError
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
async def test_run_diagnostic_step_passes_step_and_params_through():
    backend = FakeBackend()

    result = await execute_tool(
        "run_diagnostic_step", {"step": "get_dev_links_table", "params": {"device_id": "n001"}}, nucore_interface=backend
    )

    assert result == backend.step_result
    assert backend.step_calls == [("get_dev_links_table", {"device_id": "n001"})]


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
async def test_run_diagnostic_step_recovers_stringified_json_params():
    backend = FakeBackend()

    await execute_tool(
        "run_diagnostic_step",
        {"step": "services_ops", "params": '{"op": "restart", "service": "udx"}'},
        nucore_interface=backend,
    )

    assert backend.step_calls == [("services_ops", {"op": "restart", "service": "udx"})]


@pytest.mark.asyncio
async def test_run_diagnostic_step_rejects_a_non_json_string_params():
    backend = FakeBackend()

    result = await execute_tool(
        "run_diagnostic_step", {"step": "get_full_system_config", "params": "not json"}, nucore_interface=backend
    )

    assert "error" in result
    assert backend.step_calls == []


@pytest.mark.asyncio
async def test_run_diagnostic_step_rejects_non_object_params():
    backend = FakeBackend()

    result = await execute_tool(
        "run_diagnostic_step", {"step": "get_full_system_config", "params": [1, 2, 3]}, nucore_interface=backend
    )

    assert "error" in result
    assert backend.step_calls == []


@pytest.mark.asyncio
async def test_get_diagnostics_prompt_returns_the_static_prompt_text():
    backend = FakeBackend()

    result = await execute_tool("get_diagnostics_prompt", {}, nucore_interface=backend)

    assert result == _DIAGNOSTICS_PROMPT
    assert backend.step_calls == []  # doesn't touch the backend at all


@pytest.mark.asyncio
async def test_other_tools_proceed_normally_regardless_of_diagnostics():
    # No session, no gating -- diagnostics tools never block anything else.
    backend = FakeBackend()

    result = await execute_tool("get_property", {"device_id": "n001", "property": "ST"}, nucore_interface=backend)

    assert result == {"error": "no device found with id 'n001'; check DEVICE DATABASE"}

"""End-to-end: variable_op dispatched through execute_tool, calling into a
fake NuCoreInterface backend -- confirms the whole chain (args ->
NuCoreInterface.variable_ops -> response parsing) works together.
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool


class FakeResp:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.status = 200
        self.body = None
        self.calls: list = []
        self.refresh_calls = 0

    async def variable_ops(self, var_type, var_id, operation, **kwargs):
        self.calls.append((var_type, var_id, operation, kwargs))
        return FakeResp(self.status, self.body)

    async def _refresh_routines_database(self):
        self.refresh_calls += 1
        return False

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
    def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    def group_scene_update_link(self, *a, **kw): raise NotImplementedError
    def group_scene_get_node_roles(self, *a, **kw): raise NotImplementedError
    def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def run_diagnostic_step(self, step, **params): raise NotImplementedError
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError
    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self): raise NotImplementedError
    async def finish_device_discovery(self): raise NotImplementedError


@pytest.mark.asyncio
async def test_create_returns_id_from_response_no_refresh_needed():
    backend = FakeBackend()
    backend.body = {"successful": True, "data": {"id": "4", "name": "Irrigation_Mode", "prec": 0}}
    result = await execute_tool(
        "variable_op", {"type": 1, "operation": "create", "name": "Irrigation_Mode", "prec": 0}, nucore_interface=backend
    )
    assert result == {"type": 1, "id": "4", "status": "saved"}
    assert backend.calls == [(1, None, "create", {"name": "Irrigation_Mode", "prec": 0})]
    assert backend.routines_changed is True


@pytest.mark.asyncio
async def test_create_warns_when_id_cannot_be_read():
    backend = FakeBackend()
    backend.body = None
    result = await execute_tool("variable_op", {"type": 1, "operation": "create"}, nucore_interface=backend)
    assert result["status"] == "saved" and "warning" in result


@pytest.mark.asyncio
async def test_update_passes_only_given_fields():
    backend = FakeBackend()
    result = await execute_tool(
        "variable_op", {"type": 2, "id": "3", "operation": "update", "value": 1}, nucore_interface=backend
    )
    assert result == {"type": 2, "id": "3", "status": "saved"}
    assert backend.calls == [(2, "3", "update", {"value": 1})]


@pytest.mark.asyncio
async def test_update_requires_id():
    backend = FakeBackend()
    result = await execute_tool("variable_op", {"type": 1, "operation": "update", "value": 1}, nucore_interface=backend)
    assert "error" in result and "id is required" in result["error"]
    assert backend.calls == []


@pytest.mark.asyncio
async def test_delete_requires_id():
    backend = FakeBackend()
    result = await execute_tool("variable_op", {"type": 1, "operation": "delete"}, nucore_interface=backend)
    assert "error" in result and "id is required" in result["error"]


@pytest.mark.asyncio
async def test_delete_succeeds():
    backend = FakeBackend()
    result = await execute_tool("variable_op", {"type": 1, "id": "3", "operation": "delete"}, nucore_interface=backend)
    assert result == {"type": 1, "id": "3", "status": "ok"}
    assert backend.calls == [(1, "3", "delete", {})]


@pytest.mark.asyncio
async def test_rejects_bad_type_and_operation():
    backend = FakeBackend()
    assert "error" in await execute_tool("variable_op", {"type": 3, "operation": "create"}, nucore_interface=backend)
    assert "error" in await execute_tool("variable_op", {"type": 1, "operation": "rename"}, nucore_interface=backend)


@pytest.mark.asyncio
async def test_backend_rejection_is_not_reported_as_saved():
    backend = FakeBackend()
    backend.status = 400
    result = await execute_tool(
        "variable_op", {"type": 1, "id": "3", "operation": "update", "value": 1}, nucore_interface=backend
    )
    assert "error" in result and "HTTP 400" in result["error"]


@pytest.mark.asyncio
async def test_routines_changed_not_set_on_failure():
    backend = FakeBackend()
    backend.status = 400
    backend.routines_changed = False
    await execute_tool("variable_op", {"type": 1, "id": "3", "operation": "delete"}, nucore_interface=backend)
    assert backend.routines_changed is False


@pytest.mark.asyncio
async def test_list_variables_returns_condensed_variables_and_refreshes_first():
    backend = FakeBackend()
    backend.condensed_variables = [
        {"id": "1", "type": 1, "name": "Watering_the_plants", "val": 0, "init": 0, "prec": 0},
        {"id": "3", "type": 2, "name": "Irrigation_Mode", "val": 1, "init": 0, "prec": 0},
    ]
    result = await execute_tool("list_variables", {}, nucore_interface=backend)
    assert result == {"variables": backend.condensed_variables}
    assert backend.refresh_calls == 1


@pytest.mark.asyncio
async def test_list_variables_filters_by_type():
    backend = FakeBackend()
    backend.condensed_variables = [
        {"id": "1", "type": 1, "name": "Watering_the_plants", "val": 0, "init": 0, "prec": 0},
        {"id": "3", "type": 2, "name": "Irrigation_Mode", "val": 1, "init": 0, "prec": 0},
    ]
    result = await execute_tool("list_variables", {"type": 2}, nucore_interface=backend)
    assert result == {"variables": [{"id": "3", "type": 2, "name": "Irrigation_Mode", "val": 1, "init": 0, "prec": 0}]}


@pytest.mark.asyncio
async def test_list_variables_rejects_bad_type():
    backend = FakeBackend()
    result = await execute_tool("list_variables", {"type": 3}, nucore_interface=backend)
    assert "error" in result

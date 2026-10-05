"""``group_scene_op`` -- ``remove_member``/``update_link`` only.

``add_member`` was removed from this tool: it was fully redundant with
`multi_device_scene` (which makes the exact same underlying
`group_scene_add_member` call, including for a single device) except that
`group_scene_op` skipped the `availableAsController`/`availableAsResponder`
role precheck and the "controller can only be in one scene" guard that
`multi_device_scene` runs. Every add now goes through `multi_device_scene`
instead, even a single one.
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.groups = {}
        self.folders = {}
        self.remove_member_calls: list = []
        self.update_link_calls: list = []
        self.remove_result: dict = {"successful": True}
        self.update_result: dict = {"successful": True}

    async def group_scene_remove_member(self, group_address, link_address):
        self.remove_member_calls.append((group_address, link_address))
        return self.remove_result

    async def group_scene_update_link(self, group_address, controller_address, link):
        self.update_link_calls.append((group_address, controller_address, link))
        return self.update_result

    async def _load(self, **kwargs): raise NotImplementedError
    async def _load_routines(self): raise NotImplementedError
    async def _load_variables(self): raise NotImplementedError
    async def send_commands(self, commands): raise NotImplementedError
    async def create_automation_routine(self, routine): raise NotImplementedError
    async def update_routine(self, routine): raise NotImplementedError
    async def get_properties(self, device_id): raise NotImplementedError
    def get_device_name(self, device_id): raise NotImplementedError
    def get_device_id(self, device_str): raise NotImplementedError
    async def get_all_routines_summary(self): raise NotImplementedError
    async def get_routine_summary(self, routine_id): raise NotImplementedError
    async def get_all_routines(self): raise NotImplementedError
    async def get_routine(self, routine_id): raise NotImplementedError
    async def add_node(self, node_name, type): raise NotImplementedError
    async def node_ops(self, node_id, operation, **kwargs): raise NotImplementedError
    async def routine_ops(self, routine_id, operation): raise NotImplementedError
    async def variable_ops(self, var_type, var_id, operation, **kwargs): raise NotImplementedError
    async def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_node_roles(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def scene_test(self, device_id): raise NotImplementedError
    async def run_diagnostic_step(self, step, **params): raise NotImplementedError
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError
    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self): raise NotImplementedError
    async def finish_device_discovery(self): raise NotImplementedError
    async def remove_device(self, device_address, protocol=None, **kwargs): raise NotImplementedError


@pytest.mark.asyncio
async def test_remove_member_calls_the_backend_and_reports_ok():
    backend = FakeBackend()
    result = await execute_tool(
        "group_scene_op",
        {"operation": "remove_member", "group_address": "SCENE1", "link_address": "D1"},
        nucore_interface=backend,
    )

    assert result == {"group_address": "SCENE1", "link_address": "D1", "operation": "remove_member", "status": "ok"}
    assert backend.remove_member_calls == [("SCENE1", "D1")]


@pytest.mark.asyncio
async def test_update_link_requires_a_link_object():
    backend = FakeBackend()
    result = await execute_tool(
        "group_scene_op",
        {"operation": "update_link", "group_address": "SCENE1", "link_address": "D1"},
        nucore_interface=backend,
    )

    assert result == {"error": "update_link requires a 'link' object describing the new behavior"}
    assert backend.update_link_calls == []


@pytest.mark.asyncio
async def test_update_link_calls_the_backend_and_reports_ok():
    backend = FakeBackend()
    result = await execute_tool(
        "group_scene_op",
        {"operation": "update_link", "group_address": "SCENE1", "link_address": "D1", "link": {"on_level": 50}},
        nucore_interface=backend,
    )

    assert result == {"group_address": "SCENE1", "link_address": "D1", "operation": "update_link", "status": "ok"}
    assert backend.update_link_calls == [("SCENE1", "D1", {"on_level": 50})]


@pytest.mark.asyncio
async def test_backend_failure_surfaces_as_an_error():
    backend = FakeBackend()
    backend.remove_result = {"successful": False, "error": "hub rejected it"}
    result = await execute_tool(
        "group_scene_op",
        {"operation": "remove_member", "group_address": "SCENE1", "link_address": "D1"},
        nucore_interface=backend,
    )

    assert "error" in result


@pytest.mark.asyncio
async def test_add_member_is_no_longer_a_supported_operation():
    backend = FakeBackend()
    result = await execute_tool(
        "group_scene_op",
        {"operation": "add_member", "group_address": "SCENE1", "link_address": "D1"},
        nucore_interface=backend,
    )

    assert result == {"error": "unknown group_scene_op operation 'add_member'"}

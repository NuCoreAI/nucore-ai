"""delete_folder -- dispatched through execute_tool against a fake
NuCoreInterface backend. Focuses on the contents-decision flow (ask, then
preview, then execute) layered in front of the usual two-call confirm gate,
and on the "never cascade into a nested non-empty folder" safety default.
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool


class FakeResp:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.all_routines: dict[int, dict] = {}
        self.refresh_calls = 0
        self.deleted_ids: list[int] = []
        self.updated_triggers: list[dict] = []
        self.delete_status_by_id: dict[int, int] = {}
        self.update_status = 200

    async def _refresh_routines_database(self):
        self.refresh_calls += 1
        self.routines_changed = False
        return True

    async def routine_ops(self, routine_id, operation):
        self.deleted_ids.append(routine_id)
        return FakeResp(self.delete_status_by_id.get(routine_id, 200))

    async def get_routine(self, routine_id):
        return self.all_routines.get(routine_id)

    async def update_routine(self, program):
        self.updated_triggers.append(program)
        return FakeResp(self.update_status)

    # Unused by delete_folder -- stubbed only so the abstract class instantiates.
    async def _load(self, **kwargs): raise NotImplementedError
    async def _load_routines(self): raise NotImplementedError
    async def _load_variables(self): raise NotImplementedError
    async def send_commands(self, commands): raise NotImplementedError
    async def create_automation_routine(self, routine): raise NotImplementedError
    async def get_properties(self, device_id): raise NotImplementedError
    def get_device_name(self, device_id): raise NotImplementedError
    def get_device_id(self, device_str): raise NotImplementedError
    async def get_all_routines_summary(self): raise NotImplementedError
    async def get_routine_summary(self, routine_id): raise NotImplementedError
    async def get_all_routines(self): raise NotImplementedError
    async def add_node(self, node_name, type): raise NotImplementedError
    async def node_ops(self, node_id, operation, **kwargs): raise NotImplementedError
    async def variable_ops(self, var_type, var_id, operation, **kwargs): raise NotImplementedError
    async def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_update_link(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_node_roles(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def scene_test(self, device_id): raise NotImplementedError
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError
    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self, protocol=None, mode="include", **kwargs): raise NotImplementedError
    async def finish_device_discovery(self, protocol=None, **kwargs): raise NotImplementedError
    async def remove_device(self, device_address, protocol=None, **kwargs): raise NotImplementedError


@pytest.mark.asyncio
async def test_empty_folder_skips_straight_to_the_plain_confirm():
    backend = FakeBackend()
    backend.all_routines = {7: {"id": 7, "name": "Empty Folder", "parent": 0, "isFolder": True}}

    preview = await execute_tool("delete_folder", {"folder_id": 7}, nucore_interface=backend)
    assert preview["confirmation_required"] is True
    assert backend.deleted_ids == []

    result = await execute_tool("delete_folder", {"folder_id": 7, "confirmed": True}, nucore_interface=backend)
    assert result == {"folder_id": 7, "status": "ok"}
    assert backend.deleted_ids == [7]


@pytest.mark.asyncio
async def test_non_empty_folder_with_no_contents_action_asks_first_with_no_side_effects():
    backend = FakeBackend()
    backend.all_routines = {
        7: {"id": 7, "name": "Vacation Mode", "parent": 0, "isFolder": True},
        42: {"id": 42, "name": "Bedtime", "parent": 7},
    }

    result = await execute_tool("delete_folder", {"folder_id": 7}, nucore_interface=backend)

    assert result["confirmation_required"] == "choose_contents_action"
    assert result["children"] == [{"id": 42, "name": "Bedtime"}]
    assert backend.deleted_ids == [] and backend.updated_triggers == []


@pytest.mark.asyncio
async def test_contents_action_delete_previews_before_confirmed():
    backend = FakeBackend()
    backend.all_routines = {
        7: {"id": 7, "name": "Vacation Mode", "parent": 0, "isFolder": True},
        42: {"id": 42, "name": "Bedtime", "parent": 7},
    }

    result = await execute_tool(
        "delete_folder", {"folder_id": 7, "contents_action": "delete"}, nucore_interface=backend
    )

    assert result["confirmation_required"] is True
    assert result["contents_action"] == "delete"
    assert backend.deleted_ids == [] and backend.updated_triggers == []


@pytest.mark.asyncio
async def test_contents_action_delete_confirmed_deletes_children_then_folder():
    backend = FakeBackend()
    backend.all_routines = {
        7: {"id": 7, "name": "Vacation Mode", "parent": 0, "isFolder": True},
        42: {"id": 42, "name": "Bedtime", "parent": 7},
        43: {"id": 43, "name": "Sunset Lights", "parent": 7},
    }

    result = await execute_tool(
        "delete_folder",
        {"folder_id": 7, "contents_action": "delete", "confirmed": True},
        nucore_interface=backend,
    )

    assert result == {"folder_id": 7, "status": "ok", "contents_action": "delete", "affected_ids": [42, 43]}
    # Children deleted before the folder itself.
    assert backend.deleted_ids == [42, 43, 7]
    assert backend.updated_triggers == []


@pytest.mark.asyncio
async def test_contents_action_delete_aborts_folder_delete_if_a_child_fails():
    backend = FakeBackend()
    backend.all_routines = {
        7: {"id": 7, "name": "Vacation Mode", "parent": 0, "isFolder": True},
        42: {"id": 42, "name": "Bedtime", "parent": 7},
        43: {"id": 43, "name": "Sunset Lights", "parent": 7},
    }
    backend.delete_status_by_id[43] = 500

    result = await execute_tool(
        "delete_folder",
        {"folder_id": 7, "contents_action": "delete", "confirmed": True},
        nucore_interface=backend,
    )

    assert "error" in result
    # The folder itself must never be deleted if a child failed.
    assert 7 not in backend.deleted_ids
    # Bug fix: child 42 really did get deleted on the hub before 43 failed --
    # that mutation must not be left invisible to the next refresh just
    # because the overall call reports failure.
    assert backend.routines_changed is True


@pytest.mark.asyncio
async def test_contents_action_move_aborts_folder_delete_if_a_child_fails():
    backend = FakeBackend()
    backend.all_routines = {
        7: {"id": 7, "name": "Vacation Mode", "parent": 0, "isFolder": True},
        9: {"id": 9, "name": "Other Folder", "parent": 0, "isFolder": True},
        42: {"id": 42, "name": "Bedtime", "parent": 7},
        43: {"id": 43, "name": "Sunset Lights", "parent": 7},
    }
    backend.update_status = 500

    result = await execute_tool(
        "delete_folder",
        {"folder_id": 7, "contents_action": "move", "move_to_folder_id": 9, "confirmed": True},
        nucore_interface=backend,
    )

    assert "error" in result
    assert 7 not in backend.deleted_ids
    assert backend.routines_changed is True


@pytest.mark.asyncio
async def test_contents_action_move_requires_move_to_folder_id():
    backend = FakeBackend()
    backend.all_routines = {
        7: {"id": 7, "name": "Vacation Mode", "parent": 0, "isFolder": True},
        42: {"id": 42, "name": "Bedtime", "parent": 7},
    }

    result = await execute_tool(
        "delete_folder", {"folder_id": 7, "contents_action": "move"}, nucore_interface=backend
    )

    assert "error" in result
    assert backend.updated_triggers == []


@pytest.mark.asyncio
async def test_contents_action_move_confirmed_reparents_children_then_deletes_folder():
    backend = FakeBackend()
    backend.all_routines = {
        7: {"id": 7, "name": "Vacation Mode", "parent": 0, "isFolder": True},
        9: {"id": 9, "name": "Other Folder", "parent": 0, "isFolder": True},
        42: {
            "id": 42, "name": "Bedtime", "parent": 7,
            "if": [{"type": "control", "andOr": "and", "id": "DON", "node": "A", "op": "IS"}],
            "then": [{"type": "cmd", "id": "DOF", "node": "B", "p": []}],
            "else": [],
        },
    }

    result = await execute_tool(
        "delete_folder",
        {"folder_id": 7, "contents_action": "move", "move_to_folder_id": 9, "confirmed": True},
        nucore_interface=backend,
    )

    assert result == {"folder_id": 7, "status": "ok", "contents_action": "move", "affected_ids": [42]}
    assert len(backend.updated_triggers) == 1
    moved = backend.updated_triggers[0]
    assert moved["parent"] == 9
    # Everything else about the child is untouched -- a server-side field
    # mutation, not a recompile.
    assert moved["if"] == backend.all_routines[42]["if"]
    assert moved["then"] == backend.all_routines[42]["then"]
    assert backend.deleted_ids == [7]


@pytest.mark.asyncio
async def test_non_empty_nested_folder_child_is_refused_not_cascaded():
    backend = FakeBackend()
    backend.all_routines = {
        7: {"id": 7, "name": "Vacation Mode", "parent": 0, "isFolder": True},
        8: {"id": 8, "name": "Nested Folder", "parent": 7, "isFolder": True},
        99: {"id": 99, "name": "Grandchild Routine", "parent": 8},
    }

    result = await execute_tool(
        "delete_folder",
        {"folder_id": 7, "contents_action": "delete", "confirmed": True},
        nucore_interface=backend,
    )

    assert "error" in result and "Nested Folder" in result["error"]
    assert backend.deleted_ids == []


@pytest.mark.asyncio
async def test_folder_id_is_required():
    backend = FakeBackend()
    result = await execute_tool("delete_folder", {}, nucore_interface=backend)
    assert "error" in result

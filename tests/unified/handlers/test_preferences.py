"""End-to-end: list_preferences/preference_op dispatched through
execute_tool. Preferences aren't session-scoped (unlike Plan) -- plain
immediate CRUD, same shape as variable_op/list_variables.

FakeBackend gets its preferences_dir set to a tmp_path per test (rather than
pre-attaching a PreferenceStore directly) so these tests exercise the same
get_store() codepath real usage does, without ever touching a real default
location -- there isn't one (see design/user-pref.md).
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool


class FakeBackend(NuCoreInterface):
    def __init__(self, preferences_dir=None):
        super().__init__(json_output=True, formatter_type="minimal")
        self.preferences_dir = preferences_dir

    async def run_diagnostic_step(self, step, **params): raise NotImplementedError
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
async def test_not_configured_gives_a_clear_error_not_a_crash():
    backend = FakeBackend()  # preferences_dir left as None

    list_result = await execute_tool("list_preferences", {}, nucore_interface=backend)
    op_result = await execute_tool(
        "preference_op", {"operation": "create", "type": "alias", "alias": "mbr", "target": "Master Bedroom Scene"},
        nucore_interface=backend,
    )

    assert "not configured" in list_result["error"]
    assert "not configured" in op_result["error"]


@pytest.mark.asyncio
async def test_create_and_list_an_alias(tmp_path):
    backend = FakeBackend(preferences_dir=str(tmp_path))

    created = await execute_tool(
        "preference_op",
        {"operation": "create", "type": "alias", "alias": "mbr", "target": "Master Bedroom Scene"},
        nucore_interface=backend,
    )
    listed = await execute_tool("list_preferences", {"type": "alias"}, nucore_interface=backend)

    assert created == {"id": "p1", "type": "alias", "alias": "mbr", "target": "Master Bedroom Scene"}
    assert listed["preferences"] == [created]


@pytest.mark.asyncio
async def test_duplicate_alias_is_rejected_case_insensitively(tmp_path):
    backend = FakeBackend(preferences_dir=str(tmp_path))
    await execute_tool(
        "preference_op",
        {"operation": "create", "type": "alias", "alias": "mbr", "target": "Master Bedroom Scene"},
        nucore_interface=backend,
    )

    result = await execute_tool(
        "preference_op",
        {"operation": "create", "type": "alias", "alias": "MBR", "target": "Something Else"},
        nucore_interface=backend,
    )

    assert "already exists" in result["error"]


@pytest.mark.asyncio
async def test_create_an_annual_event_and_list_computes_next_occurrence(tmp_path):
    backend = FakeBackend(preferences_dir=str(tmp_path))

    created = await execute_tool(
        "preference_op",
        {
            "operation": "create",
            "type": "event",
            "name": "Dad's yahrtzeit",
            "recurrence": "annual",
            "month": 6,
            "day": 15,
            "remind_days_before": 2,
        },
        nucore_interface=backend,
    )

    assert created["type"] == "event"
    assert "next_occurrence" in created and "days_until" in created

    listed = await execute_tool("list_preferences", {"type": "event"}, nucore_interface=backend)
    assert listed["preferences"][0]["name"] == "Dad's yahrtzeit"
    assert "next_occurrence" in listed["preferences"][0]


@pytest.mark.asyncio
async def test_create_event_rejects_invalid_recurrence(tmp_path):
    backend = FakeBackend(preferences_dir=str(tmp_path))

    result = await execute_tool(
        "preference_op",
        {"operation": "create", "type": "event", "name": "Something", "recurrence": "weekly"},
        nucore_interface=backend,
    )

    assert "error" in result


@pytest.mark.asyncio
async def test_create_event_rejects_invalid_date(tmp_path):
    backend = FakeBackend(preferences_dir=str(tmp_path))

    result = await execute_tool(
        "preference_op",
        {"operation": "create", "type": "event", "name": "Something", "recurrence": "once", "date": "not-a-date"},
        nucore_interface=backend,
    )

    assert "error" in result


@pytest.mark.asyncio
async def test_delete_requires_only_id(tmp_path):
    backend = FakeBackend(preferences_dir=str(tmp_path))
    created = await execute_tool(
        "preference_op",
        {"operation": "create", "type": "alias", "alias": "mbr", "target": "Master Bedroom Scene"},
        nucore_interface=backend,
    )

    result = await execute_tool(
        "preference_op", {"operation": "delete", "id": created["id"]}, nucore_interface=backend
    )
    listed = await execute_tool("list_preferences", {}, nucore_interface=backend)

    assert result == {"id": created["id"], "status": "deleted"}
    assert listed["preferences"] == []


@pytest.mark.asyncio
async def test_delete_unknown_id_returns_an_error(tmp_path):
    backend = FakeBackend(preferences_dir=str(tmp_path))

    result = await execute_tool("preference_op", {"operation": "delete", "id": "p999"}, nucore_interface=backend)

    assert "error" in result

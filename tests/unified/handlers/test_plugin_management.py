"""End-to-end: list_store_plugins/list_purchased_plugins dispatched through
execute_tool -- confirms the store/licenses response shapes are parsed
correctly and the licenses->store nsid join resolves names.
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool

STORE_RESPONSE = {
    "successful": True,
    "data": [
        {
            "name": "Airscape",
            "author": "Jimbo.Automates",
            "desc": "Airscape Node Server",
            "nsid": "0bec5267-b1c0-44e3-aa60-e1f84d1c5291",
            "type": "python3",
            "updatedAt": "2024-05-22T03:17:26.000Z",
        },
        {
            "name": "HusqvarnaMower",
            "author": "Bob Paauwe",
            "desc": "Husqvarna Mower: A node server for control of AutoMower",
            "nsid": "c9527579-10bd-4be3-8f12-e6c40e57aabf",
            "type": "python3",
            "updatedAt": "2024-12-31T18:28:21.000Z",
        },
    ],
}

LICENSES_RESPONSE = {
    "successful": True,
    "data": [
        {"nsid": "132e8dd7-e452-41dd-80f1-6a7da660f00b", "edition": "Free", "active": True, "expiry": None},
        {
            "nsid": "0bec5267-b1c0-44e3-aa60-e1f84d1c5291",
            "edition": "Standard",
            "active": True,
            "expiry": "2026-07-04T17:41:31.000Z",
        },
    ],
}


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.store_response = STORE_RESPONSE
        self.licenses_response = LICENSES_RESPONSE

    async def get_active_plugins(self):
        return self.store_response

    async def get_purchased_plugins(self):
        return self.licenses_response

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
    def get_diagnostics_map(self): raise NotImplementedError
    async def run_diagnostics(self, function, **kwargs): raise NotImplementedError
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError


@pytest.mark.asyncio
async def test_list_store_plugins_returns_flattened_entries():
    backend = FakeBackend()
    result = await execute_tool("list_store_plugins", {}, nucore_interface=backend)
    assert result == {
        "plugins": [
            {
                "nsid": "0bec5267-b1c0-44e3-aa60-e1f84d1c5291",
                "name": "Airscape",
                "author": "Jimbo.Automates",
                "description": "Airscape Node Server",
                "type": "python3",
                "updated_at": "2024-05-22T03:17:26.000Z",
            },
            {
                "nsid": "c9527579-10bd-4be3-8f12-e6c40e57aabf",
                "name": "HusqvarnaMower",
                "author": "Bob Paauwe",
                "description": "Husqvarna Mower: A node server for control of AutoMower",
                "type": "python3",
                "updated_at": "2024-12-31T18:28:21.000Z",
            },
        ]
    }


@pytest.mark.asyncio
async def test_list_store_plugins_error_on_failed_fetch():
    backend = FakeBackend()
    backend.store_response = None
    result = await execute_tool("list_store_plugins", {}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_list_purchased_plugins_joins_name_from_store():
    backend = FakeBackend()
    result = await execute_tool("list_purchased_plugins", {}, nucore_interface=backend)
    assert result == {
        "licenses": [
            # nsid not present in the store list -- name resolves to None rather
            # than guessed at (e.g. a discontinued plugin).
            {"nsid": "132e8dd7-e452-41dd-80f1-6a7da660f00b", "name": None, "edition": "Free", "active": True, "expiry": None},
            {
                "nsid": "0bec5267-b1c0-44e3-aa60-e1f84d1c5291",
                "name": "Airscape",
                "edition": "Standard",
                "active": True,
                "expiry": "2026-07-04T17:41:31.000Z",
            },
        ]
    }


@pytest.mark.asyncio
async def test_list_purchased_plugins_error_on_failed_fetch():
    backend = FakeBackend()
    backend.licenses_response = {"successful": False}
    result = await execute_tool("list_purchased_plugins", {}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_list_purchased_plugins_still_works_if_store_fetch_fails():
    """The store call is only used for name enrichment -- its failure
    shouldn't block reporting licenses, just leave name unresolved."""
    backend = FakeBackend()
    backend.store_response = None
    result = await execute_tool("list_purchased_plugins", {}, nucore_interface=backend)
    assert result["licenses"][1]["nsid"] == "0bec5267-b1c0-44e3-aa60-e1f84d1c5291"
    assert result["licenses"][1]["name"] is None

"""End-to-end: list_store_plugins/list_purchased_plugins/list_installed_plugins
dispatched through execute_tool -- confirms the store/licenses/installed
response shapes are parsed correctly and the licenses->store nsid join
resolves names.
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


INSTALLED_RESPONSE = {
    "successful": True,
    "data": [
        {"profileNum": 3, "name": "YouTube", "isLocal": False},
    ],
}


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.store_response = STORE_RESPONSE
        self.licenses_response = LICENSES_RESPONSE
        self.installed_response = INSTALLED_RESPONSE
        self.plugin_ops_response = {"successful": True, "data": {"plugin_id": "stub", "operation": "stub"}}
        self.plugin_prompt_response = {"successful": True, "data": {"prompt": "stub prompt"}}
        self.plugin_tools_response = {"successful": True, "data": {"tools": [{"name": "stub_tool", "description": "stub", "params": {}}]}}
        self.plugin_llm_result_response = {"successful": True, "data": {"result": "stub result"}}

    async def get_active_plugins(self):
        return self.store_response

    async def get_purchased_plugins(self):
        return self.licenses_response

    async def get_installed_plugins(self):
        return self.installed_response

    async def plugin_ops(self, plugin_id, operation):
        return self.plugin_ops_response

    async def get_plugin_prompt(self, plugin_id):
        return self.plugin_prompt_response

    async def get_plugin_tools(self, plugin_id):
        return self.plugin_tools_response

    async def handle_plugin_llm_result(self, plugin_id, args):
        return self.plugin_llm_result_response

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
    async def start_diagnostics(self, **kwargs): raise NotImplementedError
    async def run_diagnostic_step(self, step, **params): raise NotImplementedError
    def get_running_diagnostic(self): return None
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError
    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self): raise NotImplementedError
    async def finish_device_discovery(self): raise NotImplementedError


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


@pytest.mark.asyncio
async def test_list_installed_plugins_maps_profile_num_to_plugin_id():
    backend = FakeBackend()
    result = await execute_tool("list_installed_plugins", {}, nucore_interface=backend)
    assert result == {
        "plugins": [
            {"plugin_id": 3, "name": "YouTube", "is_local": False},
        ]
    }


@pytest.mark.asyncio
async def test_list_installed_plugins_error_on_failed_fetch():
    backend = FakeBackend()
    backend.installed_response = None
    result = await execute_tool("list_installed_plugins", {}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_install_plugin_returns_install_link_without_completing_an_install():
    backend = FakeBackend()
    result = await execute_tool(
        "install_plugin",
        {"nsid": "0bec5267-b1c0-44e3-aa60-e1f84d1c5291", "name": "Airscape"},
        nucore_interface=backend,
    )
    assert result == {
        "install_required": True,
        "nsid": "0bec5267-b1c0-44e3-aa60-e1f84d1c5291",
        "name": "Airscape",
        "install_url": "/plugins/store/0bec5267-b1c0-44e3-aa60-e1f84d1c5291",
    }


@pytest.mark.asyncio
async def test_install_plugin_ignores_a_guessed_nsid_and_resolves_by_name():
    """Regression: the model has called install_plugin with the plugin's
    display name as nsid (e.g. "TeslaEVstream") instead of the real UUID, even
    when the real value was in the same conversation. A non-UUID nsid must be
    re-resolved against the real store list, by name, rather than trusted."""
    backend = FakeBackend()
    result = await execute_tool(
        "install_plugin", {"nsid": "Airscape", "name": "Airscape"}, nucore_interface=backend
    )
    assert result == {
        "install_required": True,
        "nsid": "0bec5267-b1c0-44e3-aa60-e1f84d1c5291",
        "name": "Airscape",
        "install_url": "/plugins/store/0bec5267-b1c0-44e3-aa60-e1f84d1c5291",
    }


@pytest.mark.asyncio
async def test_install_plugin_errors_when_nsid_and_name_are_both_unknown():
    backend = FakeBackend()
    result = await execute_tool(
        "install_plugin", {"nsid": "NotARealPlugin", "name": "Not A Real Plugin"}, nucore_interface=backend
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_install_plugin_requires_nsid():
    backend = FakeBackend()
    result = await execute_tool("install_plugin", {"name": "Cool Plugin"}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_install_plugin_requires_name():
    backend = FakeBackend()
    result = await execute_tool(
        "install_plugin", {"nsid": "0bec5267-b1c0-44e3-aa60-e1f84d1c5291"}, nucore_interface=backend
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_buy_plugin_returns_purchase_link_without_completing_a_purchase():
    backend = FakeBackend()
    result = await execute_tool(
        "buy_plugin",
        {"nsid": "c9527579-10bd-4be3-8f12-e6c40e57aabf", "name": "HusqvarnaMower"},
        nucore_interface=backend,
    )
    assert result == {
        "purchase_required": True,
        "nsid": "c9527579-10bd-4be3-8f12-e6c40e57aabf",
        "name": "HusqvarnaMower",
        "purchase_url": "/plugins/store/c9527579-10bd-4be3-8f12-e6c40e57aabf",
    }


@pytest.mark.asyncio
async def test_buy_plugin_ignores_a_guessed_nsid_and_resolves_by_name():
    backend = FakeBackend()
    result = await execute_tool(
        "buy_plugin", {"nsid": "HusqvarnaMower", "name": "HusqvarnaMower"}, nucore_interface=backend
    )
    assert result == {
        "purchase_required": True,
        "nsid": "c9527579-10bd-4be3-8f12-e6c40e57aabf",
        "name": "HusqvarnaMower",
        "purchase_url": "/plugins/store/c9527579-10bd-4be3-8f12-e6c40e57aabf",
    }


@pytest.mark.asyncio
async def test_buy_plugin_errors_when_nsid_and_name_are_both_unknown():
    backend = FakeBackend()
    result = await execute_tool(
        "buy_plugin", {"nsid": "NotARealPlugin", "name": "Not A Real Plugin"}, nucore_interface=backend
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_buy_plugin_requires_nsid():
    backend = FakeBackend()
    result = await execute_tool("buy_plugin", {"name": "Cool Plugin"}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_buy_plugin_requires_name():
    backend = FakeBackend()
    result = await execute_tool(
        "buy_plugin", {"nsid": "c9527579-10bd-4be3-8f12-e6c40e57aabf"}, nucore_interface=backend
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_delete_plugin_returns_delete_link_without_completing_a_deletion():
    backend = FakeBackend()
    result = await execute_tool("delete_plugin", {"plugin_id": "3", "name": "YouTube"}, nucore_interface=backend)
    assert result == {
        "delete_required": True,
        "plugin_id": 3,
        "name": "YouTube",
        "delete_url": "/plugins/dashboard/3",
    }


@pytest.mark.asyncio
async def test_delete_plugin_ignores_a_guessed_plugin_id_and_resolves_by_name():
    """Regression: the model has repeatedly called delete_plugin with a
    slugified name as plugin_id (e.g. "sun" for a plugin actually named
    "Sun", profileNum 6) instead of the real plugin_id, even when the real
    value was in the same conversation. The handler must not trust the given
    plugin_id -- it re-resolves against the real installed list, falling back
    to a name match when the given id doesn't correspond to any of them."""
    backend = FakeBackend()
    backend.installed_response = {
        "successful": True,
        "data": [{"profileNum": 6, "name": "Sun", "isLocal": False}],
    }
    result = await execute_tool("delete_plugin", {"plugin_id": "sun", "name": "Sun"}, nucore_interface=backend)
    assert result == {
        "delete_required": True,
        "plugin_id": 6,
        "name": "Sun",
        "delete_url": "/plugins/dashboard/6",
    }


@pytest.mark.asyncio
async def test_delete_plugin_errors_when_not_actually_installed():
    # A non-numeric, non-matching plugin_id/name falls through to the name
    # lookup in NuCoreInterface._get_plugin_number and finds nothing -- unlike
    # a bare digit string, which is trusted as a real profileNum without
    # verification (same as the rest of the id-resolution fast paths).
    backend = FakeBackend()
    result = await execute_tool(
        "delete_plugin", {"plugin_id": "not-a-real-plugin", "name": "Not Installed"}, nucore_interface=backend
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_delete_plugin_requires_plugin_id():
    backend = FakeBackend()
    result = await execute_tool("delete_plugin", {"name": "Cool Plugin"}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_delete_plugin_requires_name():
    backend = FakeBackend()
    result = await execute_tool("delete_plugin", {"plugin_id": "3"}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_get_plugin_capabilities_combines_prompt_and_tools():
    backend = FakeBackend()
    result = await execute_tool("get_plugin_capabilities", {"plugin_id": "3"}, nucore_interface=backend)
    assert result == {
        "plugin_id": 3,
        "prompt": "stub prompt",
        "tools": [{"name": "stub_tool", "description": "stub", "params": {}}],
    }


@pytest.mark.asyncio
async def test_get_plugin_capabilities_requires_plugin_id():
    backend = FakeBackend()
    result = await execute_tool("get_plugin_capabilities", {}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_get_plugin_capabilities_error_when_prompt_fetch_fails():
    backend = FakeBackend()
    backend.plugin_prompt_response = {"successful": False}
    result = await execute_tool("get_plugin_capabilities", {"plugin_id": "3"}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_get_plugin_capabilities_error_when_tools_fetch_fails():
    backend = FakeBackend()
    backend.plugin_tools_response = {"successful": False}
    result = await execute_tool("get_plugin_capabilities", {"plugin_id": "3"}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_call_plugin_returns_stub_result():
    backend = FakeBackend()
    backend.plugin_llm_result_response = {"successful": True, "data": {"result": "42"}}
    result = await execute_tool(
        "call_plugin", {"plugin_id": "3", "tool_name": "3_get_status", "args": {}}, nucore_interface=backend
    )
    assert result == {"result": "42"}


@pytest.mark.asyncio
async def test_call_plugin_requires_plugin_id_and_tool_name():
    backend = FakeBackend()
    result = await execute_tool("call_plugin", {"tool_name": "x"}, nucore_interface=backend)
    assert "error" in result
    result = await execute_tool("call_plugin", {"plugin_id": "3"}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_call_plugin_error_on_failed_result():
    backend = FakeBackend()
    backend.plugin_llm_result_response = {"successful": False}
    result = await execute_tool(
        "call_plugin", {"plugin_id": "3", "tool_name": "3_get_status"}, nucore_interface=backend
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_call_plugin_strips_plugin_id_prefix_and_embeds_tool_name():
    """The plugin_id prefix is a global-uniqueness convention on this side --
    the plugin's own handle_plugin_llm_result should see its bare tool name,
    passed inside the single args payload (not as a separate parameter)."""
    backend = FakeBackend()
    captured = {}

    async def fake_handle_plugin_llm_result(plugin_id, args):
        captured["plugin_id"] = plugin_id
        captured["args"] = args
        return {"successful": True, "data": {"ok": True}}

    backend.handle_plugin_llm_result = fake_handle_plugin_llm_result

    await execute_tool(
        "call_plugin",
        {"plugin_id": "3", "tool_name": "3_get_status", "args": {"foo": "bar"}},
        nucore_interface=backend,
    )

    assert captured["plugin_id"] == 3
    assert captured["args"] == {"foo": "bar", "tool_name": "get_status"}

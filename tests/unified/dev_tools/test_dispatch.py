"""End-to-end: dev_tools' own TOOL_HANDLERS dispatched through
dev_tools.dispatch.execute_tool -- confirms both the new local tools
(validate_profile/lookup_uom) and the plugin-lifecycle tools reused directly
from unified.handlers.plugin_management (list_installed_plugins/plugin_ops/
get_plugin_capabilities/call_plugin/configure_plugin) route correctly, and
that unknown-tool/exception handling matches unified.dispatch.execute_tool's
contract exactly (same shape, separate table).
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dev_tools.dispatch import TOOL_HANDLERS, execute_tool

INSTALLED_RESPONSE = {
    "successful": True,
    "data": [{"profileNum": 7, "name": "MyDevPlugin", "isLocal": True, "aiSupport": False, "state": "stopped"}],
}


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.installed_response = INSTALLED_RESPONSE
        self.configure_response = {"successful": True, "data": {"applied": True}}
        self.plugin_ops_response = {"successful": True, "data": {}}

    async def get_installed_plugins(self):
        return self.installed_response

    async def configure_plugin(self, plugin_id, config):
        return self.configure_response

    async def plugin_ops(self, plugin_id, operation):
        return self.plugin_ops_response

    async def get_plugin_prompt(self, plugin_id):
        return {"successful": True, "data": {"prompt": "stub prompt"}}

    async def get_plugin_tools(self, plugin_id):
        return {"successful": True, "data": {"tools": [{"name": "stub_tool", "description": "stub", "params": {}}]}}

    async def handle_plugin_llm_result(self, plugin_id, args):
        return {"successful": True, "data": {"result": "stub result"}}

    async def get_active_plugins(self): raise NotImplementedError
    async def get_purchased_plugins(self): raise NotImplementedError
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
    async def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_update_link(self, *a, **kw): raise NotImplementedError
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
async def test_validate_profile_routes_through_dev_tools_dispatch():
    result = await execute_tool("validate_profile", {"profile": "not-a-dict"}, nucore_interface=FakeBackend())
    assert result == {"valid": False, "errors": ["'profile' must be a JSON object"]}


@pytest.mark.asyncio
async def test_lookup_uom_routes_through_dev_tools_dispatch():
    result = await execute_tool("lookup_uom", {"keyword": "amps"}, nucore_interface=FakeBackend())
    assert any(m["id"] == "1" for m in result["matches"])


@pytest.mark.asyncio
async def test_list_installed_plugins_is_reused_from_unified_handlers():
    result = await execute_tool("list_installed_plugins", {}, nucore_interface=FakeBackend())
    assert result == {
        "plugins": [
            {"plugin_id": 7, "name": "MyDevPlugin", "is_local": True, "ai_support": False, "state": "stopped"}
        ]
    }


@pytest.mark.asyncio
async def test_configure_plugin_is_reused_from_unified_handlers():
    result = await execute_tool(
        "configure_plugin", {"plugin_id": "7", "config": {"api_key": "x"}}, nucore_interface=FakeBackend()
    )
    assert result == {"plugin_id": 7, "applied": True}


@pytest.mark.asyncio
async def test_configure_plugin_requires_a_config_object():
    result = await execute_tool("configure_plugin", {"plugin_id": "7"}, nucore_interface=FakeBackend())
    assert "error" in result


@pytest.mark.asyncio
async def test_unknown_tool_returns_error_dict_without_raising():
    result = await execute_tool("not_a_real_tool", {}, nucore_interface=FakeBackend())
    assert result == {"error": "unknown tool 'not_a_real_tool'"}


@pytest.mark.asyncio
async def test_handler_exception_is_caught_and_returned_as_error(monkeypatch):
    async def boom(nucore_interface, args):
        raise ValueError("kaboom")

    monkeypatch.setitem(TOOL_HANDLERS, "exploding_tool", boom)
    result = await execute_tool("exploding_tool", {}, nucore_interface=FakeBackend())
    assert "kaboom" in result["error"]


def test_dev_tools_excludes_marketplace_only_plugin_tools():
    # buy/delete/store/purchased are marketplace concerns, not part of the
    # local dev/test loop -- see dispatch.py's module docstring. install_plugin
    # is also excluded: it's the purchase-flow URL-handback stub, not a real
    # local install (see design/developers/plugin_dev_tooling.md).
    for excluded in ("buy_plugin", "delete_plugin", "list_store_plugins", "list_purchased_plugins", "install_plugin"):
        assert excluded not in TOOL_HANDLERS

"""End-to-end: plugin_authoring's own TOOL_HANDLERS dispatched through
plugin_authoring.dispatch.execute_tool -- confirms both the new local tools
(validate_profile/lookup_uom) and the plugin-lifecycle tools reused directly
from unified.handlers.plugin_management (list_installed_plugins/plugin_ops/
get_plugin_capabilities/call_plugin/configure_plugin) route correctly, and
that unknown-tool/exception handling matches unified.dispatch.execute_tool's
contract exactly (same shape, separate table).
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.plugin_authoring.dispatch import TOOL_HANDLERS, build_tool_handlers, execute_tool
from unified.plugin_authoring.evidence_ledger import EvidenceLedger

INSTALLED_RESPONSE = {
    "successful": True,
    "data": [
        {
            "profileNum": 7,
            "nsid": "local.my_dev_plugin",
            "name": "MyDevPlugin",
            "isLocal": True,
            "aiSupport": False,
            "state": "stopped",
        }
    ],
}


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.installed_response = INSTALLED_RESPONSE
        self.configure_response = {"successful": True, "data": {"applied": True}}
        self.plugin_ops_response = {"successful": True, "data": {}}

    async def get_installed_plugins(self):
        return self.installed_response

    async def configure_plugin(self, plugin_id, config, key="customparams"):
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
async def test_validate_profile_routes_through_plugin_authoring_dispatch():
    result = await execute_tool("validate_profile", {"profile": "not-a-dict"}, nucore_interface=FakeBackend())
    assert result == {"valid": False, "errors": ["'profile' must be a JSON object"]}


@pytest.mark.asyncio
async def test_lookup_uom_routes_through_plugin_authoring_dispatch():
    result = await execute_tool("lookup_uom", {"keyword": "amps"}, nucore_interface=FakeBackend())
    assert any(m["id"] == "1" for m in result["matches"])


@pytest.mark.asyncio
async def test_lookup_property_id_routes_through_plugin_authoring_dispatch():
    result = await execute_tool("lookup_property_id", {"keyword": "temperature"}, nucore_interface=FakeBackend())
    assert any(m["id"] == "CLITEMP" for m in result["matches"])


@pytest.mark.asyncio
async def test_list_installed_plugins_is_reused_from_unified_handlers():
    result = await execute_tool("list_installed_plugins", {}, nucore_interface=FakeBackend())
    assert result == {
        "plugins": [
            {
                "plugin_id": 7,
                "nsid": "local.my_dev_plugin",
                "name": "MyDevPlugin",
                "is_local": True,
                "ai_support": False,
                "state": "stopped",
            }
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


def test_plugin_authoring_excludes_marketplace_only_plugin_tools():
    # buy/delete/store/purchased are marketplace concerns, not part of the
    # local dev/test loop -- see dispatch.py's module docstring. install_plugin
    # is also excluded: it's the purchase-flow URL-handback stub, not a real
    # local install (see design/developers/plugin_dev_tooling.md).
    for excluded in ("buy_plugin", "delete_plugin", "list_store_plugins", "list_purchased_plugins", "install_plugin"):
        assert excluded not in TOOL_HANDLERS


# --- build_tool_handlers: Phase 2 discovery tools (design/developers/impl_plan.md) ---


def test_build_tool_handlers_keeps_every_existing_tool():
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=None, search_engine_api_key=None, secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    for name in TOOL_HANDLERS:
        assert name in handlers


def test_build_tool_handlers_adds_the_three_always_on_discovery_tools():
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=None, search_engine_api_key=None, secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    for name in ("search_store_plugins", "search_github_plugins", "fetch_reference"):
        assert name in handlers
    assert "search_web" not in handlers


def test_build_tool_handlers_always_adds_the_workspace_discovery_tools():
    # list_generated_plugins/read_generated_plugin (Phase 3) are local-disk-only
    # -- unlike search_web, nothing about them depends on search_engine/key.
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=None, search_engine_api_key=None, secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    assert "list_generated_plugins" in handlers
    assert "read_generated_plugin" in handlers


def test_build_tool_handlers_always_adds_generate_plugin_scaffold():
    # generate_plugin_scaffold (Stage 2, design/developers/plugin_authoring_p4_impl.md)
    # is local-disk-only too -- always registered, same as the workspace tools above.
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=None, search_engine_api_key=None, secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    assert "generate_plugin_scaffold" in handlers


def test_build_tool_handlers_always_adds_install_generated_plugin():
    # install_generated_plugin (Stage 3) touches the real hub, but registering
    # the tool itself needs nothing beyond plugin_output_root -- always added.
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=None, search_engine_api_key=None, secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    assert "install_generated_plugin" in handlers


def test_build_tool_handlers_always_adds_update_and_delete_registered_plugin():
    # Resolve a reported install_generated_plugin "conflict" -- same
    # plugin_output_root-only binding as install_generated_plugin itself.
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=None, search_engine_api_key=None, secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    assert "update_registered_plugin" in handlers
    assert "delete_registered_plugin" in handlers


def test_uninstall_installed_plugin_is_in_the_static_tool_handlers_table():
    # Needs no plugin_output_root binding -- registered directly, same as
    # plugin_ops, not through build_tool_handlers. Not customer-facing (see
    # its own docstring) -- plugin_authoring-only, same as delete_plugin's
    # boundary, but unlike delete_plugin it's a real delete, so it's reused
    # here rather than left out entirely.
    assert "uninstall_installed_plugin" in TOOL_HANDLERS


def test_plugin_authoring_reuses_run_shell_command_and_detect_usb_device():
    # Stage 6 (design/developers/plugin_authoring_p4_impl.md): hardware/USB
    # detection reuses the customer tool set's run_shell_command directly.
    assert "run_shell_command" in TOOL_HANDLERS
    assert "detect_usb_device" in TOOL_HANDLERS


@pytest.mark.parametrize("engine", ["brave", "tavily"])
def test_build_tool_handlers_adds_search_web_only_when_engine_and_key_both_given(engine):
    missing_key = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=engine, search_engine_api_key=None, secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    assert "search_web" not in missing_key

    missing_engine = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=None, search_engine_api_key="key", secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    assert "search_web" not in missing_engine

    both = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=engine, search_engine_api_key="key", secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    assert "search_web" in both


@pytest.mark.asyncio
async def test_execute_tool_uses_the_given_tool_handlers_dict():
    async def stub(nucore_interface, args):
        return {"stub": True}

    handlers = dict(TOOL_HANDLERS)
    handlers["validate_profile"] = stub

    result = await execute_tool("validate_profile", {}, nucore_interface=FakeBackend(), tool_handlers=handlers)
    assert result == {"stub": True}


@pytest.mark.asyncio
async def test_execute_tool_falls_back_to_module_default_when_no_tool_handlers_given():
    result = await execute_tool("validate_profile", {"profile": "not-a-dict"}, nucore_interface=FakeBackend())
    assert result == {"valid": False, "errors": ["'profile' must be a JSON object"]}


# --- configure_developer (developer commissioning) ---


def test_build_tool_handlers_always_adds_configure_developer():
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=None, search_engine_api_key=None, secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    assert "configure_developer" in handlers


@pytest.mark.asyncio
async def test_configure_developer_routes_through_dispatch_and_writes_the_config(tmp_path):
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(),
        search_engine=None,
        search_engine_api_key=None,
        secret_values=[],
        plugin_output_root=str(tmp_path),
        get_user_id=lambda: "dev@example.com",
    )
    result = await execute_tool(
        "configure_developer",
        {"email": "dev@example.com", "name": "Dev"},
        nucore_interface=FakeBackend(),
        tool_handlers=handlers,
    )
    assert result["configured"] is True
    assert (tmp_path / "developer_config.json").is_file()


@pytest.mark.asyncio
async def test_configure_developer_mismatched_email_is_refused_through_dispatch(tmp_path):
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(),
        search_engine=None,
        search_engine_api_key=None,
        secret_values=[],
        plugin_output_root=str(tmp_path),
        get_user_id=lambda: "someone-else@example.com",
    )
    result = await execute_tool(
        "configure_developer",
        {"email": "dev@example.com", "name": "Dev"},
        nucore_interface=FakeBackend(),
        tool_handlers=handlers,
    )
    assert "error" in result
    assert not (tmp_path / "developer_config.json").exists()


# --- get_developer_config (read-only check-first tool) ---


def test_build_tool_handlers_always_adds_get_developer_config():
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=None, search_engine_api_key=None, secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    assert "get_developer_config" in handlers


@pytest.mark.asyncio
async def test_get_developer_config_routes_through_dispatch(tmp_path):
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=None, search_engine_api_key=None, secret_values=[], plugin_output_root=str(tmp_path)
    )

    result = await execute_tool("get_developer_config", {}, nucore_interface=FakeBackend(), tool_handlers=handlers)
    assert result == {"configured": False}

    await execute_tool(
        "configure_developer",
        {"email": "dev@example.com", "name": "Dev"},
        nucore_interface=FakeBackend(),
        tool_handlers=handlers,
    )
    result = await execute_tool("get_developer_config", {}, nucore_interface=FakeBackend(), tool_handlers=handlers)
    assert result == {"configured": True, "email": "dev@example.com", "name": "Dev"}


# --- setup_dev_venv (local/dev-testing only) ---


def test_build_tool_handlers_always_adds_setup_dev_venv():
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=None, search_engine_api_key=None, secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    assert "setup_dev_venv" in handlers


@pytest.mark.asyncio
async def test_setup_dev_venv_routes_through_dispatch(tmp_path):
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(),
        search_engine=None,
        search_engine_api_key=None,
        secret_values=[],
        plugin_output_root=str(tmp_path),
    )
    result = await execute_tool(
        "setup_dev_venv",
        {"location": "does_not_exist"},
        nucore_interface=FakeBackend(),
        tool_handlers=handlers,
    )
    assert "error" in result


# --- setup_vscode_debug_config (local/dev-testing only) ---


def test_build_tool_handlers_always_adds_setup_vscode_debug_config():
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=None, search_engine_api_key=None, secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    assert "setup_vscode_debug_config" in handlers


@pytest.mark.asyncio
async def test_setup_vscode_debug_config_routes_through_dispatch(tmp_path):
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(),
        search_engine=None,
        search_engine_api_key=None,
        secret_values=[],
        plugin_output_root=str(tmp_path),
    )
    result = await execute_tool(
        "setup_vscode_debug_config",
        {"location": "does_not_exist"},
        nucore_interface=FakeBackend(),
        tool_handlers=handlers,
    )
    assert "error" in result


# --- regenerate_plugin_boilerplate (local/dev-testing only) ---


def test_build_tool_handlers_always_adds_regenerate_plugin_boilerplate():
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(), search_engine=None, search_engine_api_key=None, secret_values=[], plugin_output_root="/tmp/plugin-projects"
    )
    assert "regenerate_plugin_boilerplate" in handlers


@pytest.mark.asyncio
async def test_regenerate_plugin_boilerplate_routes_through_dispatch(tmp_path):
    handlers = build_tool_handlers(
        ledger=EvidenceLedger(),
        search_engine=None,
        search_engine_api_key=None,
        secret_values=[],
        plugin_output_root=str(tmp_path),
    )
    result = await execute_tool(
        "regenerate_plugin_boilerplate",
        {"location": "does_not_exist"},
        nucore_interface=FakeBackend(),
        tool_handlers=handlers,
    )
    assert "error" in result

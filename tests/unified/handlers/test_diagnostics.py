"""End-to-end: the eight standalone diagnostic tools dispatched through
execute_tool -- confirms TOOL_HANDLERS routing, required-arg validation, and
that the old session-wrapper tools (start_diagnostics/run_diagnostic_step)
are gone. No session/session_id involvement at all -- these are ordinary,
always-available tools, same as get_property/node_op.
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import TOOL_HANDLERS, execute_tool


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.calls: list[tuple[str, dict]] = []
        self.result = {"ok": True}

    async def diagnostics_get_full_system_config(self, **kwargs):
        self.calls.append(("get_full_system_config", kwargs))
        return self.result

    async def diagnostics_get_device_family(self, device_id, **kwargs):
        self.calls.append(("get_device_family", {"device_id": device_id, **kwargs}))
        return self.result

    async def diagnostics_get_dev_links_table(self, device_id, **kwargs):
        self.calls.append(("get_dev_links_table", {"device_id": device_id, **kwargs}))
        return self.result

    async def diagnostics_get_iox_links_table(self, device_id, **kwargs):
        self.calls.append(("get_iox_links_table", {"device_id": device_id, **kwargs}))
        return self.result

    async def diagnostics_compare_device_links(self, device_id, **kwargs):
        self.calls.append(("compare_device_links", {"device_id": device_id, **kwargs}))
        return self.result

    async def diagnostics_get_all_plm_links(self, refresh_plm_links=False, **kwargs):
        self.calls.append(("get_all_plm_links", {"refresh_plm_links": refresh_plm_links, **kwargs}))
        return self.result

    async def diagnostics_quick_plm_sanity_check(self, **kwargs):
        self.calls.append(("quick_plm_sanity_check", kwargs))
        return self.result

    async def begin_plm_op(self, step): raise NotImplementedError
    async def end_plm_op(self): raise NotImplementedError
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
    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self): raise NotImplementedError
    async def finish_device_discovery(self): raise NotImplementedError


@pytest.mark.asyncio
async def test_get_full_system_config_dispatches():
    backend = FakeBackend()
    result = await execute_tool("get_full_system_config", {}, nucore_interface=backend)
    assert result == backend.result
    assert backend.calls == [("get_full_system_config", {})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["get_device_family", "get_dev_links_table", "get_iox_links_table", "compare_device_links"],
)
async def test_device_id_tools_dispatch_with_device_id(tool_name):
    backend = FakeBackend()
    result = await execute_tool(tool_name, {"device_id": "n001"}, nucore_interface=backend)
    assert result == backend.result
    assert backend.calls == [(tool_name, {"device_id": "n001"})]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name",
    ["get_device_family", "get_dev_links_table", "get_iox_links_table", "compare_device_links"],
)
async def test_device_id_tools_require_device_id(tool_name):
    backend = FakeBackend()
    result = await execute_tool(tool_name, {}, nucore_interface=backend)
    assert "error" in result
    assert backend.calls == []


@pytest.mark.asyncio
async def test_get_all_plm_links_defaults_refresh_to_false():
    backend = FakeBackend()
    result = await execute_tool("get_all_plm_links", {}, nucore_interface=backend)
    assert result == backend.result
    assert backend.calls == [("get_all_plm_links", {"refresh_plm_links": False})]


@pytest.mark.asyncio
async def test_get_all_plm_links_passes_refresh_true():
    backend = FakeBackend()
    await execute_tool("get_all_plm_links", {"refresh_plm_links": True}, nucore_interface=backend)
    assert backend.calls == [("get_all_plm_links", {"refresh_plm_links": True})]


@pytest.mark.asyncio
async def test_quick_plm_sanity_check_dispatches():
    backend = FakeBackend()
    result = await execute_tool("quick_plm_sanity_check", {}, nucore_interface=backend)
    assert result == backend.result
    assert backend.calls == [("quick_plm_sanity_check", {})]


def test_old_session_tools_are_gone():
    assert "start_diagnostics" not in TOOL_HANDLERS
    assert "run_diagnostic_step" not in TOOL_HANDLERS


@pytest.mark.asyncio
async def test_calling_a_removed_tool_returns_unknown_tool_error():
    backend = FakeBackend()
    result = await execute_tool("start_diagnostics", {}, nucore_interface=backend)
    assert result == {"error": "unknown tool 'start_diagnostics'"}


@pytest.mark.asyncio
async def test_get_diagnostics_prompt_returns_the_prose_without_touching_the_backend():
    # This tool is static content (prompt/diagnostics.md), not backend data --
    # confirms it never calls into nucore_interface at all, unlike the other
    # seven diagnostic tools.
    from unified.handlers.diagnostics import _DIAGNOSTICS_PROMPT

    backend = FakeBackend()
    result = await execute_tool("get_diagnostics_prompt", {}, nucore_interface=backend)
    assert result == _DIAGNOSTICS_PROMPT
    assert "How INSTEON links work" in result
    assert backend.calls == []


@pytest.mark.asyncio
async def test_diagnostics_tools_are_not_blocked_by_anything_diagnostics_related():
    # There is no diagnostics blanket lock any more -- two diagnostics tools
    # back to back both just dispatch normally, no gating between them at
    # the dispatch layer (the four PLM-exclusive tools' own mutual exclusion
    # is enforced inside IoXDiagnostics, not here -- see tests/iox).
    backend = FakeBackend()
    first = await execute_tool("get_full_system_config", {}, nucore_interface=backend)
    second = await execute_tool("quick_plm_sanity_check", {}, nucore_interface=backend)
    assert first == backend.result
    assert second == backend.result

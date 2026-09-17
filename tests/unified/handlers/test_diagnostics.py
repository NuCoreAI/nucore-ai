"""End-to-end: the diagnostic-adjacent tools dispatched through execute_tool --
confirms the thin pass-through to NuCoreInterface, and that these tools never
gate on or interfere with anything else. diagnostics_not_responding/
diagnostics_no_status_feedback/restart_core_service only need to prove
they're thin pass-throughs here; see tests/iox/test_insteon_diagnose.py for
the real investigation logic behind the first two.
"""

from __future__ import annotations

from typing import Any

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.full_system_config_result: Any = {"INSTEON Enabled": True}
        self.core_services_status_result: Any = {"isy": "running"}
        self.device_family_result: Any = "insteon"
        self.device_family_calls: list[str] = []
        self.diagnose_not_responding_result: Any = {"diagnosis": "sentinel not-responding"}
        self.diagnose_not_responding_calls: list[tuple[str, str | None]] = []
        self.diagnose_no_status_feedback_result: Any = {"diagnosis": "sentinel no-status-feedback"}
        self.diagnose_no_status_feedback_calls: list[tuple[str, str | None]] = []
        self.restart_core_service_result: Any = {"status": "restarted"}
        self.restart_core_service_calls: list[tuple[str, str]] = []

    async def get_full_system_config(self):
        return self.full_system_config_result

    async def get_core_services_status(self):
        return self.core_services_status_result

    async def get_device_family(self, device_id):
        self.device_family_calls.append(device_id)
        return self.device_family_result

    async def diagnose_not_responding(self, protocol, device_id=None):
        self.diagnose_not_responding_calls.append((protocol, device_id))
        return self.diagnose_not_responding_result

    async def diagnose_no_status_feedback(self, protocol, device_id=None):
        self.diagnose_no_status_feedback_calls.append((protocol, device_id))
        return self.diagnose_no_status_feedback_result

    async def restart_core_service(self, service, operation):
        self.restart_core_service_calls.append((service, operation))
        return self.restart_core_service_result

    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self): raise NotImplementedError
    async def finish_device_discovery(self): raise NotImplementedError
    async def remove_device(self, device_address, protocol=None, **kwargs): raise NotImplementedError

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
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError


@pytest.mark.asyncio
async def test_get_full_system_config_calls_the_backend_directly():
    backend = FakeBackend()

    result = await execute_tool("get_full_system_config", {}, nucore_interface=backend)

    assert result == backend.full_system_config_result


@pytest.mark.asyncio
async def test_get_core_services_status_calls_the_backend_directly():
    backend = FakeBackend()

    result = await execute_tool("get_core_services_status", {}, nucore_interface=backend)

    assert result == backend.core_services_status_result


@pytest.mark.asyncio
async def test_get_device_family_calls_the_backend_directly_with_device_id():
    backend = FakeBackend()

    result = await execute_tool("get_device_family", {"device_id": "n001"}, nucore_interface=backend)

    assert result == backend.device_family_result
    assert backend.device_family_calls == ["n001"]


@pytest.mark.asyncio
async def test_get_device_family_requires_device_id():
    backend = FakeBackend()

    result = await execute_tool("get_device_family", {}, nucore_interface=backend)

    assert "error" in result
    assert backend.device_family_calls == []


@pytest.mark.asyncio
async def test_diagnostics_not_responding_forwards_protocol_and_device_id():
    backend = FakeBackend()

    result = await execute_tool(
        "diagnostics_not_responding", {"protocol": "insteon", "device_id": "n001"}, nucore_interface=backend
    )

    assert result == backend.diagnose_not_responding_result
    assert backend.diagnose_not_responding_calls == [("insteon", "n001")]


@pytest.mark.asyncio
async def test_diagnostics_not_responding_requires_protocol():
    backend = FakeBackend()

    result = await execute_tool("diagnostics_not_responding", {}, nucore_interface=backend)

    assert "error" in result
    assert backend.diagnose_not_responding_calls == []


@pytest.mark.asyncio
async def test_diagnostics_no_status_feedback_forwards_protocol_and_optional_device_id():
    backend = FakeBackend()

    result = await execute_tool("diagnostics_no_status_feedback", {"protocol": "insteon"}, nucore_interface=backend)

    assert result == backend.diagnose_no_status_feedback_result
    assert backend.diagnose_no_status_feedback_calls == [("insteon", None)]


@pytest.mark.asyncio
async def test_diagnostics_no_status_feedback_requires_protocol():
    backend = FakeBackend()

    result = await execute_tool("diagnostics_no_status_feedback", {}, nucore_interface=backend)

    assert "error" in result
    assert backend.diagnose_no_status_feedback_calls == []


@pytest.mark.asyncio
async def test_restart_core_service_forwards_service_and_operation():
    backend = FakeBackend()

    result = await execute_tool(
        "restart_core_service", {"service": "isy", "operation": "restart"}, nucore_interface=backend
    )

    assert result == backend.restart_core_service_result
    assert backend.restart_core_service_calls == [("isy", "restart")]


@pytest.mark.asyncio
async def test_restart_core_service_requires_service_and_operation():
    backend = FakeBackend()

    result = await execute_tool("restart_core_service", {"service": "isy"}, nucore_interface=backend)

    assert "error" in result
    assert backend.restart_core_service_calls == []


@pytest.mark.asyncio
async def test_other_tools_proceed_normally_regardless_of_diagnostics():
    # No session, no gating -- diagnostics tools never block anything else.
    backend = FakeBackend()

    result = await execute_tool("get_property", {"device_id": "n001", "property": "ST"}, nucore_interface=backend)

    assert result == {"error": "no device found with id 'n001'; check DEVICE DATABASE"}

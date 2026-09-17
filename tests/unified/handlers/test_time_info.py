"""End-to-end: get_time_info dispatched through execute_tool -- confirms the
thin pass-through to NuCoreInterface.get_timespecs() and render_time_specs's
key-renaming (snake_case backend keys -> the same display names TIME &
LOCATION already uses in the prompt).
"""

from __future__ import annotations

from typing import Any

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool
from unified.handlers.time_info import render_time_specs


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.timespecs_result: Any = {
            "current_time": "2026-09-17T08:15:00-07:00",
            "timezone": "America/Los_Angeles",
            "latitude": 34.05,
            "longitude": -118.233,
            "sunrise": "2026-09-17T06:32:00-07:00",
            "sunset": "2026-09-17T19:15:00-07:00",
        }

    async def get_timespecs(self):
        return self.timespecs_result

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


def test_render_time_specs_renames_keys_to_prompt_display_names():
    result = render_time_specs(
        {
            "current_time": "2026-09-17T08:15:00-07:00",
            "timezone": "America/Los_Angeles",
            "latitude": 34.05,
            "longitude": -118.233,
            "sunrise": "2026-09-17T06:32:00-07:00",
            "sunset": "2026-09-17T19:15:00-07:00",
        }
    )
    assert result == {
        "CURRENT_TIME": "2026-09-17T08:15:00-07:00",
        "TIMEZONE": "America/Los_Angeles",
        "LATITUDE": 34.05,
        "LONGITUDE": -118.233,
        "SUNRISE_TODAY": "2026-09-17T06:32:00-07:00",
        "SUNSET_TODAY": "2026-09-17T19:15:00-07:00",
    }


def test_render_time_specs_omits_missing_fields():
    result = render_time_specs({"timezone": "America/Los_Angeles"})
    assert result == {"TIMEZONE": "America/Los_Angeles"}


@pytest.mark.asyncio
async def test_get_time_info_calls_the_backend_directly():
    backend = FakeBackend()

    result = await execute_tool("get_time_info", {}, nucore_interface=backend)

    assert result == render_time_specs(backend.timespecs_result)
    assert result["CURRENT_TIME"] == "2026-09-17T08:15:00-07:00"
    assert result["SUNRISE_TODAY"] == "2026-09-17T06:32:00-07:00"


@pytest.mark.asyncio
async def test_get_time_info_reports_a_clear_error_when_unavailable():
    backend = FakeBackend()
    backend.timespecs_result = None

    result = await execute_tool("get_time_info", {}, nucore_interface=backend)

    assert "error" in result


@pytest.mark.asyncio
async def test_get_time_info_reports_a_clear_error_when_not_implemented():
    class NotImplementedBackend(FakeBackend):
        async def get_timespecs(self):
            raise NotImplementedError

    result = await execute_tool("get_time_info", {}, nucore_interface=NotImplementedBackend())

    assert "error" in result

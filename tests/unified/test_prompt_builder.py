"""Confirms build_system_prompt substitutes both database placeholders (no
stray <<...>> left over). Variables deliberately have no standing prompt
section (see list_variables tool) -- only their per-routine cross-reference
(variable_names, sourced from condensed_routines) shows up here.
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.prompt_builder import build_system_prompt


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.condensed_routines = [
            {"id": 1, "name": "Bedtime", "comment": "", "device_names": [], "variable_names": ["Irrigation_Mode"]}
        ]

    async def _refresh_routines_database(self):
        return False

    async def _load(self, **kwargs): raise NotImplementedError
    async def _load_routines(self): raise NotImplementedError
    async def _load_variables(self): raise NotImplementedError
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
async def test_build_system_prompt_substitutes_every_placeholder():
    prompt = await build_system_prompt(FakeBackend())

    assert "<<" not in prompt and ">>" not in prompt
    assert "# VARIABLES DATABASE" not in prompt
    assert "Bedtime" in prompt
    assert "Irrigation_Mode" in prompt  # via ROUTINES DATABASE's variable_names cross-reference


@pytest.mark.asyncio
async def test_build_system_prompt_reports_time_info_unavailable_when_not_implemented():
    # FakeBackend doesn't override get_timespecs -- the base class's
    # NotImplementedError must be swallowed, not crash prompt building.
    prompt = await build_system_prompt(FakeBackend())

    assert "Time/timezone/location information is unavailable" in prompt


@pytest.mark.asyncio
async def test_build_system_prompt_includes_time_info_when_available():
    class TimeAwareBackend(FakeBackend):
        async def get_timespecs(self):
            return {
                "current_time": "2026-06-02T02:02:44-07:00",
                "timezone": "America/Los_Angeles",
                "latitude": 34.05,
                "longitude": -118.233,
                "sunrise": "2026-05-29T05:43:41-07:00",
                "sunset": "2026-05-29T19:56:42-07:00",
            }

    prompt = await build_system_prompt(TimeAwareBackend())

    assert "TIMEZONE = 'America/Los_Angeles'" in prompt
    assert "LATITUDE = 34.05" in prompt
    assert "SUNRISE_TODAY = '2026-05-29T05:43:41-07:00'" in prompt


@pytest.mark.asyncio
async def test_build_system_prompt_reports_preferences_not_configured_by_default():
    # FakeBackend never sets preferences_dir -- there's no default location
    # (see design/user-pref.md), so this must render a clear message, not
    # crash or silently invent a path.
    prompt = await build_system_prompt(FakeBackend())

    assert "Preferences are not configured for this installation" in prompt


@pytest.mark.asyncio
async def test_build_system_prompt_reports_no_aliases_yet_when_configured_but_empty(tmp_path):
    backend = FakeBackend()
    backend.preferences_dir = str(tmp_path)

    prompt = await build_system_prompt(backend)

    assert "No aliases saved yet" in prompt


@pytest.mark.asyncio
async def test_build_system_prompt_includes_saved_aliases(tmp_path):
    from unified.preferences.preference_store import get_store

    backend = FakeBackend()
    backend.preferences_dir = str(tmp_path)
    get_store(backend).add("alias", alias="mbr", target="Master Bedroom Scene")

    prompt = await build_system_prompt(backend)

    assert "'mbr': 'Master Bedroom Scene'" in prompt

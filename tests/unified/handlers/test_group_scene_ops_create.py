"""``multi_device_scene``'s group-creation path -- when no group_address is
given, it creates one via add_node() then waits -- event-driven, via
wait_until (_event_wait.py) -- for the new group to appear by name, rather
than assuming one refresh is enough.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.handlers import group_scene_ops as group_scene_ops_module
from unified.handlers.group_scene_ops import multi_device_scene


class _FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.groups = {}
        self.folders = {}
        self.add_node_calls: list = []
        self.add_member_calls: list = []
        self._pending_group_name: str | None = None

    async def add_node(self, node_name, type):
        self.add_node_calls.append((node_name, type))
        self._pending_group_name = node_name
        return SimpleNamespace(status_code=200)

    async def _refresh_device_structure(self):
        if self._pending_group_name is not None:
            self.groups[f"g_{self._pending_group_name}"] = SimpleNamespace(name=self._pending_group_name)
            self._pending_group_name = None

    async def group_scene_get_node_roles(self, node_address):
        return {"data": {"availableAsController": True, "availableAsResponder": True}}

    async def group_scene_add_member(self, group_address, link_address, is_controller, name=None):
        self.add_member_calls.append((group_address, link_address, is_controller, name))
        return {"successful": True}

    async def _load(self, **kwargs): raise NotImplementedError
    async def _load_routines(self): raise NotImplementedError
    async def _load_variables(self): raise NotImplementedError
    async def send_commands(self, commands): raise NotImplementedError
    async def create_automation_routine(self, routine): raise NotImplementedError
    async def update_routine(self, routine): raise NotImplementedError
    async def get_properties(self, device_id): raise NotImplementedError
    def get_device_name(self, device_id): raise NotImplementedError
    def get_device_id(self, device_str): raise NotImplementedError
    async def get_all_routines_summary(self): raise NotImplementedError
    async def get_routine_summary(self, routine_id): raise NotImplementedError
    async def get_all_routines(self): raise NotImplementedError
    async def get_routine(self, routine_id): raise NotImplementedError
    async def node_ops(self, node_id, operation, **kwargs): raise NotImplementedError
    async def routine_ops(self, routine_id, operation): raise NotImplementedError
    async def variable_ops(self, var_type, var_id, operation, **kwargs): raise NotImplementedError
    async def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_update_link(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def run_diagnostic_step(self, step, **params): raise NotImplementedError
    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self, protocol=None, mode="include", **kwargs): raise NotImplementedError
    async def finish_device_discovery(self, protocol=None, **kwargs): raise NotImplementedError
    async def remove_device(self, device_address, protocol=None, **kwargs): raise NotImplementedError
    async def _subscribe_events(self, on_message_callback, on_connect_callback=None, on_disconnect_callback=None):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_creates_a_group_and_finds_its_address_after_the_refresh_that_surfaces_it():
    backend = _FakeBackend()
    result = await multi_device_scene(backend, {
        "group_name": "Movie Night",
        "devices": [{"link_address": "D1", "role": "responder"}],
    })

    assert result["group_address"] == "g_Movie Night"
    assert result["summary"]["successful"] == 1
    assert backend.add_node_calls == [("Movie Night", "group")]
    assert backend.add_member_calls == [("g_Movie Night", "D1", False, None)]


@pytest.mark.asyncio
async def test_waits_for_a_real_event_before_the_group_address_appears():
    backend = _FakeBackend()
    calls = {"n": 0}

    async def flaky_refresh():
        calls["n"] += 1
        if calls["n"] >= 2:
            backend.groups["g_Movie Night"] = SimpleNamespace(name="Movie Night")

    backend._refresh_device_structure = flaky_refresh

    async def fire_event_shortly():
        await asyncio.sleep(0.02)
        backend._dispatch_event_listeners("_3", "GD", "g_Movie Night", {})

    asyncio.create_task(fire_event_shortly())

    result = await multi_device_scene(backend, {
        "group_name": "Movie Night",
        "devices": [{"link_address": "D1", "role": "responder"}],
    })

    assert result["group_address"] == "g_Movie Night"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_errors_if_the_group_address_never_appears(monkeypatch):
    monkeypatch.setattr(group_scene_ops_module, "_GROUP_CREATE_WAIT_TIMEOUT_S", 0.05)
    backend = _FakeBackend()

    async def never_finds_it():
        pass

    backend._refresh_device_structure = never_finds_it

    result = await multi_device_scene(backend, {
        "group_name": "Movie Night",
        "devices": [{"link_address": "D1", "role": "responder"}],
    })

    assert "error" in result

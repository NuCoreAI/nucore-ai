"""unified.handlers.node_ops.node_op -- confirms _op_ok/_op_error correctly
treat a plain error string returned by NuCoreInterface.node_ops (e.g. the
Z-Wave/Zigbee/Matter/plugin delete-family guard added in iox_wrapper.py) as a
failure surfaced via {"error": ...}, not mistaken for a successful response.

Also covers add_group/add_folder's post-create id lookup, which waits --
event-driven, via wait_until (_event_wait.py) -- for the newly created node
to appear by name, rather than assuming one refresh is enough.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.handlers import node_ops as node_ops_module
from unified.handlers.node_ops import node_op


class _FakeBackend:
    def __init__(self, node_ops_result):
        self._node_ops_result = node_ops_result
        self.calls: list = []

    async def node_ops(self, node_id, operation, **kwargs):
        self.calls.append((node_id, operation, kwargs))
        return self._node_ops_result


@pytest.mark.asyncio
async def test_delete_rejected_by_backend_guard_surfaces_as_error():
    redirect_message = (
        "Cannot delete a zwave device via node_op -- the hub must be put into "
        "removal mode instead. Use pair_device(protocol=\"zwave\", "
        "action=\"exclude\", device_address=<this device's address>) -- it opens "
        "removal mode and waits for the customer to complete it on their own screen."
    )
    backend = _FakeBackend(redirect_message)
    result = await node_op(backend, {"operation": "delete", "node_id": "n1"})
    assert result == {"error": f"'delete' failed: {redirect_message}"}


@pytest.mark.asyncio
async def test_delete_success_still_reports_ok():
    backend = _FakeBackend(SimpleNamespace(status_code=200))
    result = await node_op(backend, {"operation": "delete", "node_id": "n1"})
    assert result == {"node_id": "n1", "operation": "delete", "status": "ok"}


@pytest.mark.asyncio
async def test_move_to_a_real_parent_forwards_it_unchanged():
    backend = _FakeBackend(SimpleNamespace(status_code=200))
    result = await node_op(backend, {"operation": "move", "node_id": "n1", "new_parent_id": "70326"})
    assert result == {"node_id": "n1", "operation": "move", "status": "ok"}
    assert backend.calls == [("n1", "move", {"new_parent_id": "70326"})]


@pytest.mark.asyncio
async def test_move_with_no_new_parent_id_means_root_not_an_error():
    # Regression: an omitted/empty new_parent_id used to be rejected outright
    # ("move requires new_parent_id") -- it actually means "move to the top
    # level/root", a real, valid request, not a missing argument.
    backend = _FakeBackend(SimpleNamespace(status_code=200))
    result = await node_op(backend, {"operation": "move", "node_id": "n1"})
    assert result == {"node_id": "n1", "operation": "move", "status": "ok"}
    assert backend.calls == [("n1", "move", {"new_parent_id": ""})]


@pytest.mark.asyncio
async def test_move_with_explicit_empty_new_parent_id_also_means_root():
    backend = _FakeBackend(SimpleNamespace(status_code=200))
    result = await node_op(backend, {"operation": "move", "node_id": "n1", "new_parent_id": ""})
    assert result == {"node_id": "n1", "operation": "move", "status": "ok"}
    assert backend.calls == [("n1", "move", {"new_parent_id": ""})]


class _CreateFakeBackend(NuCoreInterface):
    """Real NuCoreInterface subclass -- add_group/add_folder's id lookup
    goes through wait_until, which needs register_listener/
    unregister_listener (both inherited, unstubbed)."""

    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.groups = {}
        self.folders = {}
        self.add_node_calls: list = []
        self.refresh_calls = 0
        self._pending_name: str | None = None
        self._pending_type: str | None = None

    async def add_node(self, node_name, type):
        self.add_node_calls.append((node_name, type))
        self._pending_name = node_name
        self._pending_type = type
        return SimpleNamespace(status_code=200)

    async def _refresh_device_structure(self):
        self.refresh_calls += 1
        if self._pending_name is not None:
            registry = self.groups if self._pending_type == "group" else self.folders
            registry[f"n_{self._pending_name}"] = SimpleNamespace(name=self._pending_name)
            self._pending_name = None

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
    async def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_update_link(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_node_roles(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def run_diagnostic_step(self, step, **params): raise NotImplementedError
    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self, protocol=None, mode="include", **kwargs): raise NotImplementedError
    async def finish_device_discovery(self, protocol=None, **kwargs): raise NotImplementedError
    async def remove_device(self, device_address, protocol=None, **kwargs): raise NotImplementedError
    async def _subscribe_events(self, on_message_callback, on_connect_callback=None, on_disconnect_callback=None):
        raise NotImplementedError


@pytest.mark.asyncio
async def test_add_group_finds_its_id_after_the_refresh_that_surfaces_it():
    backend = _CreateFakeBackend()
    result = await node_op(backend, {"operation": "add_group", "new_name": "Movie Night"})
    assert result == {"operation": "add_group", "new_name": "Movie Night", "node_id": "n_Movie Night", "status": "ok"}
    assert backend.add_node_calls == [("Movie Night", "group")]


@pytest.mark.asyncio
async def test_add_folder_finds_its_id_after_the_refresh_that_surfaces_it():
    backend = _CreateFakeBackend()
    result = await node_op(backend, {"operation": "add_folder", "new_name": "Upstairs"})
    assert result == {"operation": "add_folder", "new_name": "Upstairs", "node_id": "n_Upstairs", "status": "ok"}
    assert backend.add_node_calls == [("Upstairs", "folder")]


@pytest.mark.asyncio
async def test_add_group_waits_for_a_real_event_before_the_id_appears(monkeypatch):
    backend = _CreateFakeBackend()
    calls = {"n": 0}

    async def flaky_refresh():
        calls["n"] += 1
        if calls["n"] >= 2:
            backend.groups["n_Movie Night"] = SimpleNamespace(name="Movie Night")

    backend._refresh_device_structure = flaky_refresh

    async def fire_event_shortly():
        await asyncio.sleep(0.02)
        backend._dispatch_event_listeners("_3", "GD", "n_Movie Night", {})

    asyncio.create_task(fire_event_shortly())

    result = await node_op(backend, {"operation": "add_group", "new_name": "Movie Night"})

    assert result["status"] == "ok"
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_add_group_errors_if_the_id_never_appears(monkeypatch):
    monkeypatch.setattr(node_ops_module, "_CREATE_WAIT_TIMEOUT_S", 0.05)
    backend = _CreateFakeBackend()

    async def never_finds_it():
        pass

    backend._refresh_device_structure = never_finds_it

    result = await node_op(backend, {"operation": "add_group", "new_name": "Movie Night"})

    assert "error" in result

"""IoXDiagnostics's persistent subsystem-status listener --
``_SubsystemStatusListener`` (replacing the old hardcoded
``control in ["_21", "_25", "_27", "_28"]`` branch in
``IoXWrapper._on_device_event``) and ``_apply_subsystem_status_event``, the
handler behind it.

Unlike ``insteon_diag.py``'s ``_LinksTableWaiter`` (scoped to one operation,
unregistered when done), this listener is started once and runs for the
whole process's life -- ``process()`` never returns. Tests here dispatch
events via ``_dispatch_event_listeners`` (no real websocket/hardware
involved, same pattern as ``tests/nucore/test_event_listener_registry.py``)
and poll for the background thread to have applied the resulting state
change, since there's no ``join()`` to wait on for a listener that never
finishes.
"""

from __future__ import annotations

import time

import pytest

from nucore.nucore_interface import NuCoreInterface
from iox.diagnostics.iox_diagnostics import IoXDiagnostics


class FakeBackend(NuCoreInterface):
    """Minimal concrete NuCoreInterface -- only enough to instantiate;
    IoXDiagnostics only ever touches register_listener/unregister_listener/
    _dispatch_event_listeners (inherited, not stubbed) via the listener
    under test."""

    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")

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
    async def add_node(self, node_name, type): raise NotImplementedError
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


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


@pytest.fixture
def diag():
    backend = FakeBackend()
    d = IoXDiagnostics(backend)
    d.start_subsystem_status_listener()
    yield d


def test_start_registers_all_four_controls(diag):
    backend = diag._iox_wrapper
    listener_id = diag._subsystem_listener._listener_id
    keys = set(backend._event_listeners.keys())

    assert (listener_id, "_21", None) in keys
    assert (listener_id, "_25", None) in keys
    assert (listener_id, "_27", None) in keys
    assert (listener_id, "_28", None) in keys


def test_start_is_idempotent(diag):
    listener = diag._subsystem_listener

    diag.start_subsystem_status_listener()  # must not raise (duplicate registration)

    assert diag._subsystem_listener is listener


def test_listener_is_a_persistent_daemon_thread(diag):
    listener = diag._subsystem_listener
    assert listener.daemon is True
    assert listener.is_alive()


def test_listener_sets_enabled_on_status_1(diag):
    backend = diag._iox_wrapper

    backend._dispatch_event_listeners("_25", "1.1", "n001", None)

    assert _wait_until(lambda: diag._subsystem_state["_25"]["enabled"] is True)


@pytest.mark.parametrize("status", ["2", "3"])
def test_listener_sets_connected_on_status_2_or_3(diag, status):
    backend = diag._iox_wrapper

    backend._dispatch_event_listeners("_27", f"1.{status}", "n001", None)

    assert _wait_until(lambda: diag._subsystem_state["_27"]["connected"] is True)


def test_listener_ignores_non_status_subsystem_property(diag):
    backend = diag._iox_wrapper

    backend._dispatch_event_listeners("_28", "2.1", "n001", None)
    # give the background thread a chance to (not) apply anything
    time.sleep(0.05)

    assert diag._subsystem_state["_28"]["enabled"] is False
    assert diag._subsystem_state["_28"]["connected"] is False


def test_listener_ignores_unknown_status_value_and_keeps_running(diag):
    backend = diag._iox_wrapper

    backend._dispatch_event_listeners("_21", "1.9", "n001", None)
    time.sleep(0.05)

    assert diag._subsystem_state["_21"]["enabled"] is False
    assert diag._subsystem_state["_21"]["connected"] is False
    assert diag._subsystem_listener.is_alive()

    # the listener must still be processing events afterward
    backend._dispatch_event_listeners("_21", "1.1", "n001", None)
    assert _wait_until(lambda: diag._subsystem_state["_21"]["enabled"] is True)


def test_listener_survives_a_raising_handler_and_keeps_processing(diag, monkeypatch):
    real_handler = diag._apply_subsystem_status_event
    calls = []

    def flaky_handler(node, control, action, eventInfo):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return real_handler(node, control, action, eventInfo)

    monkeypatch.setattr(diag, "_apply_subsystem_status_event", flaky_handler)
    backend = diag._iox_wrapper

    backend._dispatch_event_listeners("_25", "1.1", "n001", None)  # raises inside the listener thread
    assert _wait_until(lambda: len(calls) >= 1)
    assert diag._subsystem_listener.is_alive()  # not killed by the exception

    backend._dispatch_event_listeners("_25", "1.1", "n001", None)  # processed normally
    assert _wait_until(lambda: diag._subsystem_state["_25"]["enabled"] is True)

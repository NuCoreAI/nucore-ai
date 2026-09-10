"""NuCoreInterface's event-listener registry: register_listener/
unregister_listener/_dispatch_event_listeners -- exact and wildcard action
matching, duplicate/type-check rejection, and the real cross-thread proof
that a listener registered on one thread is correctly notified from a
genuinely different thread with no bridging code involved.
"""

from __future__ import annotations

import threading
import time

import pytest

from nucore.device_event_listener import DeviceEventListener
from nucore.nucore_interface import NuCoreInterface


class FakeBackend(NuCoreInterface):
    """Minimal concrete NuCoreInterface -- only enough to instantiate; none
    of these stubs are exercised by the registry/listener tests below."""

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


class _CaptureListener(DeviceEventListener):
    """process() waits for exactly one item and returns it."""

    def process(self):
        try:
            return self._queue.get(timeout=5)
        except Exception:
            return None


def test_register_listener_rejects_non_device_event_listener():
    backend = FakeBackend()

    with pytest.raises(TypeError):
        backend.register_listener("id1", "_3", "ND", object())


def test_register_listener_rejects_duplicate_key():
    backend = FakeBackend()
    listener1 = _CaptureListener(backend, "_3", "ND", listener_id="dup")

    with pytest.raises(ValueError):
        backend.register_listener("dup", "_3", "ND", listener1)


def test_unregister_listener_is_idempotent():
    backend = FakeBackend()
    backend.unregister_listener("never-registered", "_3", "ND")
    backend.unregister_listener("never-registered", "_3", "ND")  # no error


def test_self_unregisters_after_process_returns():
    backend = FakeBackend()
    listener = _CaptureListener(backend, "_3", "ND", listener_id="self-unreg")
    assert ("self-unreg", "_3", "ND") in backend._event_listeners

    listener.notify("n001", "_3", "ND", None)
    listener.start()
    listener.join(timeout=2)

    assert ("self-unreg", "_3", "ND") not in backend._event_listeners


def test_dispatch_matches_exact_action():
    backend = FakeBackend()
    listener = _CaptureListener(backend, "_3", "ND")
    listener.start()

    backend._dispatch_event_listeners("_3", "NR", "n001", None)   # wrong action -- ignored
    backend._dispatch_event_listeners("_3", "ND", "n002", {"x": 1})  # matches

    listener.join(timeout=2)
    assert listener.result == ("n002", "_3", "ND", {"x": 1})


def test_dispatch_matches_wildcard_action():
    backend = FakeBackend()
    listener = _CaptureListener(backend, "_3", None)  # wildcard
    listener.start()

    backend._dispatch_event_listeners("_3", "NN", "n001", None)  # any action matches

    listener.join(timeout=2)
    assert listener.result == ("n001", "_3", "NN", None)


def test_dispatch_notifies_multiple_listeners_on_the_same_key():
    backend = FakeBackend()
    listener_a = _CaptureListener(backend, "_3", "ND", listener_id="a")
    listener_b = _CaptureListener(backend, "_3", "ND", listener_id="b")
    listener_a.start()
    listener_b.start()

    backend._dispatch_event_listeners("_3", "ND", "n001", None)

    listener_a.join(timeout=2)
    listener_b.join(timeout=2)
    assert listener_a.result == ("n001", "_3", "ND", None)
    assert listener_b.result == ("n001", "_3", "ND", None)


def test_dispatch_survives_a_listener_whose_notify_raises():
    backend = FakeBackend()

    class _BrokenListener(DeviceEventListener):
        def notify(self, *a, **kw):
            raise RuntimeError("broken listener")

        def process(self):
            return None

    broken = _BrokenListener(backend, "_3", "ND", listener_id="broken")
    good = _CaptureListener(backend, "_3", "ND", listener_id="good")
    broken.start()
    good.start()

    # must not raise, and the good listener still gets notified
    backend._dispatch_event_listeners("_3", "ND", "n001", None)

    good.join(timeout=2)
    broken.join(timeout=2)
    assert good.result == ("n001", "_3", "ND", None)


def test_dispatch_from_a_genuinely_different_thread_notifies_promptly():
    """The real point of this design: on_device_event runs on the
    subscriber's own thread and calls straight into notify() with no
    call_soon_threadsafe/run_coroutine_threadsafe bridging -- prove that
    actually works across a real thread boundary, not just within one
    thread."""
    backend = FakeBackend()
    listener = _CaptureListener(backend, "_3", "ND")
    listener.start()

    def fire_from_another_thread():
        backend._dispatch_event_listeners("_3", "ND", "n001", {"info": "payload"})

    dispatcher = threading.Thread(target=fire_from_another_thread)
    start = time.monotonic()
    dispatcher.start()
    dispatcher.join(timeout=2)
    listener.join(timeout=2)
    elapsed = time.monotonic() - start

    assert listener.result == ("n001", "_3", "ND", {"info": "payload"})
    assert elapsed < 1.0  # woke promptly, not via any polling interval

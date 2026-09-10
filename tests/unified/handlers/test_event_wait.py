"""``_event_wait.py``'s two waiters, shared by pair_device/node_ops/
group_scene_ops/routine_automation:

- ``wait_until``: the event-driven replacement for a fixed poll/sleep loop.
  Covers condition already true before any wait, waking on a real dispatched
  event, giving up once total_timeout elapses, and always unregistering its
  passive listener on every exit path.
- ``wait_for_event``: a one-shot wait for exactly one (control, action)
  event (used by pair_device's include/exclude for each protocol's own
  "pairing session ended" signal). Covers returning True/False promptly on
  arrival/timeout, exact (not wildcard) action matching, and unregistering.
- ``wait_for_node_event``: like wait_for_event, but returns the matched
  event's own ``node`` field instead of a bare bool (used by zwave exclude
  to read the removed device's address directly off the _3/"NR" event).
  Covers returning the node address promptly on arrival, None on timeout,
  and unregistering.
- ``wait_for_matching_event``: registers a wildcard listener on one control
  and waits for whichever event satisfies a caller-supplied predicate (used
  by zigbee exclude to distinguish an actual node removal from the hub
  silently just disabling the node instead). Covers a non-matching event
  being discarded while waiting continues, returning the full matched event
  tuple, timing out with None, and unregistering.
"""

from __future__ import annotations

import asyncio

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.handlers._event_wait import wait_for_event, wait_for_matching_event, wait_for_node_event, wait_until


class FakeBackend(NuCoreInterface):
    """Minimal concrete NuCoreInterface -- only enough to instantiate; none
    of these stubs are exercised by wait_until, which only needs
    register_listener/unregister_listener/_dispatch_event_listeners (all
    inherited, unstubbed)."""

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


@pytest.mark.asyncio
async def test_returns_true_immediately_if_condition_already_true():
    backend = FakeBackend()
    refresh_calls = {"n": 0}

    async def refresh():
        refresh_calls["n"] += 1

    ok = await wait_until(backend, "_3", None, lambda: True, refresh, total_timeout=5)

    assert ok is True
    assert refresh_calls["n"] == 1  # checked once, before any wait
    assert backend._event_listeners == {}  # unregistered


@pytest.mark.asyncio
async def test_wakes_on_a_dispatched_event_and_then_condition_passes():
    backend = FakeBackend()
    state = {"ready": False}

    async def refresh():
        pass

    async def fire_event_shortly():
        await asyncio.sleep(0.02)
        state["ready"] = True
        backend._dispatch_event_listeners("_3", "ND", "n001", {})

    asyncio.create_task(fire_event_shortly())

    ok = await wait_until(backend, "_3", None, lambda: state["ready"], refresh, total_timeout=2)

    assert ok is True
    assert backend._event_listeners == {}


@pytest.mark.asyncio
async def test_returns_false_once_total_timeout_elapses_with_no_event():
    backend = FakeBackend()

    async def refresh():
        pass

    ok = await wait_until(backend, "_3", None, lambda: False, refresh, total_timeout=0.05)

    assert ok is False
    assert backend._event_listeners == {}


@pytest.mark.asyncio
async def test_unregisters_even_if_condition_raises():
    backend = FakeBackend()

    def bad_condition():
        raise RuntimeError("boom")

    async def refresh():
        pass

    with pytest.raises(RuntimeError):
        await wait_until(backend, "_3", None, bad_condition, refresh, total_timeout=1)

    assert backend._event_listeners == {}


@pytest.mark.asyncio
async def test_action_wildcard_matches_any_action_for_the_control():
    backend = FakeBackend()
    state = {"ready": False}

    async def refresh():
        pass

    async def fire_unrelated_then_matching():
        await asyncio.sleep(0.01)
        backend._dispatch_event_listeners("_1", "0", "n001", {})  # wrong control -- ignored
        await asyncio.sleep(0.01)
        state["ready"] = True
        backend._dispatch_event_listeners("_3", "RV", "n001", {})  # different action, same control

    asyncio.create_task(fire_unrelated_then_matching())

    ok = await wait_until(backend, "_3", None, lambda: state["ready"], refresh, total_timeout=2)

    assert ok is True


# ---------------------------------------------------------------------------
# wait_for_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_event_returns_true_when_the_event_arrives():
    backend = FakeBackend()

    async def fire_event_shortly():
        await asyncio.sleep(0.02)
        backend._dispatch_event_listeners("_25", "2.1", None, {})

    asyncio.create_task(fire_event_shortly())

    ok = await wait_for_event(backend, "_25", "2.1", total_timeout=2)

    assert ok is True
    assert backend._event_listeners == {}


@pytest.mark.asyncio
async def test_wait_for_event_returns_false_on_timeout():
    backend = FakeBackend()

    ok = await wait_for_event(backend, "_25", "2.1", total_timeout=0.05)

    assert ok is False
    assert backend._event_listeners == {}


@pytest.mark.asyncio
async def test_wait_for_event_ignores_a_differently_actioned_event_on_the_same_control():
    # Unlike wait_until's wildcard (action=None) support, wait_for_event
    # registers on one exact action -- a different action on the same
    # control must not satisfy it.
    backend = FakeBackend()

    async def fire_wrong_action():
        await asyncio.sleep(0.02)
        backend._dispatch_event_listeners("_25", "2.2", None, {})  # "include active", not "2.1"

    asyncio.create_task(fire_wrong_action())

    ok = await wait_for_event(backend, "_25", "2.1", total_timeout=0.1)

    assert ok is False


# ---------------------------------------------------------------------------
# wait_for_node_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_node_event_returns_the_node_address_when_it_arrives():
    backend = FakeBackend()

    async def fire_event_shortly():
        await asyncio.sleep(0.02)
        backend._dispatch_event_listeners("_3", "NR", "ZW001", {})

    asyncio.create_task(fire_event_shortly())

    node = await wait_for_node_event(backend, "_3", "NR", total_timeout=2)

    assert node == "ZW001"
    assert backend._event_listeners == {}


@pytest.mark.asyncio
async def test_wait_for_node_event_returns_none_on_timeout():
    backend = FakeBackend()

    node = await wait_for_node_event(backend, "_3", "NR", total_timeout=0.05)

    assert node is None
    assert backend._event_listeners == {}


# ---------------------------------------------------------------------------
# wait_for_matching_event
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_for_matching_event_returns_the_full_tuple_on_a_match():
    backend = FakeBackend()

    async def fire_events_shortly():
        await asyncio.sleep(0.01)
        backend._dispatch_event_listeners("_3", "GD", "n001", {})  # unrelated -- discarded
        await asyncio.sleep(0.01)
        backend._dispatch_event_listeners("_3", "EN", "n002", {"enabled": "false"})

    asyncio.create_task(fire_events_shortly())

    event = await wait_for_matching_event(
        backend, "_3",
        lambda node, control, action, event_info: action == "EN" and event_info.get("enabled") == "false",
        total_timeout=2,
    )

    assert event == ("n002", "_3", "EN", {"enabled": "false"})
    assert backend._event_listeners == {}


@pytest.mark.asyncio
async def test_wait_for_matching_event_returns_none_on_timeout():
    backend = FakeBackend()

    event = await wait_for_matching_event(backend, "_3", lambda *a: False, total_timeout=0.05)

    assert event is None
    assert backend._event_listeners == {}

"""IoXWrapper._on_device_event's "_5" (System Busy Events) handling --
tracks nucore_interface.system_busy from the hub's own busy/not-busy
reports, per subscription_events.md's `_5` table (action "0" =
DEVINTIX_SYSTEM_IS_NOT_BUSY_ACTION, "1" = DEVINTIX_SYSTEM_IS_BUSY_ACTION).
Consumed by pair_device.py's _is_device_usable -- a device shouldn't be
handed back as ready for a scene/routine call while the hub still reports
itself busy, same reasoning as waiting for node.node_def to resolve.
"""

from __future__ import annotations

import threading

import pytest

from iox.iox_wrapper import IoXWrapper


def _bare_wrapper() -> IoXWrapper:
    wrapper = object.__new__(IoXWrapper)
    wrapper.nodes = {}
    wrapper.system_busy = False
    wrapper._event_listeners = {}
    wrapper._event_listeners_lock = threading.Lock()
    return wrapper


def _event(control: str, action_value: str | None, node: str | None = None) -> dict:
    return {
        "control": control,
        "action": {"value": action_value, "uom": None, "prec": None},
        "node": node,
        "eventInfo": None,
    }


async def test_system_busy_defaults_false():
    wrapper = _bare_wrapper()
    assert wrapper.system_busy is False


async def test_action_1_sets_system_busy_true():
    wrapper = _bare_wrapper()
    await wrapper._on_device_event(_event("_5", "1"))
    assert wrapper.system_busy is True


async def test_action_0_clears_system_busy():
    wrapper = _bare_wrapper()
    wrapper.system_busy = True
    await wrapper._on_device_event(_event("_5", "0"))
    assert wrapper.system_busy is False


@pytest.mark.parametrize("other_action", ["2", "3"])
async def test_other_5_actions_leave_system_busy_unchanged(other_action):
    # "2" (idle) / "3" (safe mode) are distinct states from busy/not-busy --
    # only "0"/"1" are documented as the busy/not-busy toggle.
    wrapper = _bare_wrapper()
    wrapper.system_busy = True
    await wrapper._on_device_event(_event("_5", other_action))
    assert wrapper.system_busy is True

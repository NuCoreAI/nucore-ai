"""Shared event-driven replacement for the "sleep, then refresh and check"
pattern used by several handlers (device pairing/exclusion, group/folder
creation, routine creation) to wait for a REST call's effect to become
visible in ``nucore_interface``'s local state.

The websocket subscriber (``IoXWrapper._on_device_event``) runs on its own
background thread with its own event loop -- the shared ``httpx.AsyncClient``
(and hence any ``nucore_interface`` refresh call) is bound to the *caller's*
loop, not that thread's, so the actual refresh+condition-check must happen
back on the caller's own coroutine. ``_RegisteredWaiter`` is therefore never
started as a ``Thread`` -- it only exists as a passive, registered
``notify()`` target; ``wait_until`` itself drains its queue directly.
"""

from __future__ import annotations

import asyncio
import queue
import time
from typing import Awaitable, Callable

from nucore import DeviceEventListener, NuCoreInterface


class _RegisteredWaiter(DeviceEventListener):
    """Registered as a notify() target but never started as a Thread -- see
    module docstring. ``process()`` is required by the base class but is
    never actually invoked (``.start()``/``.run()`` are never called)."""

    def process(self):
        return None


async def wait_until(
    nucore_interface: NuCoreInterface,
    control: str,
    action: str | None,
    condition: Callable[[], bool],
    refresh: Callable[[], Awaitable[None]],
    total_timeout: float,
) -> bool:
    """Event-driven replacement for a fixed poll/sleep loop.

    Registers a passive listener for *(control, action)* (``action=None``
    matches any action for that control -- a spurious wakeup is harmless
    since *condition()* is the real gate, exactly like the old poll loops'
    per-iteration check regardless of what triggered it), then loops:
    ``await refresh()``, check *condition()* (checked immediately, before any
    wait too -- the state may already satisfy it), return ``True`` once
    satisfied. Otherwise blocks on the listener's queue (via
    ``asyncio.to_thread``, not ``asyncio.sleep``) for up to the remaining
    budget, waking instantly on a real matching event instead of on a fixed
    interval. Returns ``False`` if *total_timeout* elapses with *condition()*
    never satisfied. Always unregisters before returning.
    """
    waiter = _RegisteredWaiter(nucore_interface, control, action)
    deadline = time.monotonic() + total_timeout
    try:
        while True:
            await refresh()
            if condition():
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                await asyncio.to_thread(waiter._queue.get, timeout=remaining)
            except queue.Empty:
                return False
    finally:
        nucore_interface.unregister_listener(waiter._listener_id, control, action)


async def wait_for_event(
    nucore_interface: NuCoreInterface,
    control: str,
    action: str | None,
    total_timeout: float,
) -> bool:
    """Block until ONE event matching *(control, action)* arrives, or
    *total_timeout* elapses -- for a one-shot terminal signal (e.g. a
    discovery/pairing session's own "complete" event, which fires at most
    once per session) rather than a re-checkable state condition.

    Unlike ``wait_until``, this does not loop re-checking a condition on
    every wakeup -- once the single registered event type has been observed
    (or the timeout expires), the caller is expected to do its own final
    state check regardless of which happened; waiting for a *second*
    occurrence of a one-shot event would just block for the rest of the
    budget for nothing.

    Returns ``True`` if the event arrived, ``False`` on timeout. Always
    unregisters before returning.
    """
    waiter = _RegisteredWaiter(nucore_interface, control, action)
    try:
        await asyncio.to_thread(waiter._queue.get, timeout=total_timeout)
        return True
    except queue.Empty:
        return False
    finally:
        nucore_interface.unregister_listener(waiter._listener_id, control, action)


async def wait_for_matching_event(
    nucore_interface: NuCoreInterface,
    control: str,
    predicate: Callable[[str, str, str, object], bool],
    total_timeout: float,
) -> tuple[str, str, str, object] | None:
    """Block until an event on *control* (any action -- registers with
    action=None, since which action(s) count is exactly what *predicate*
    decides) satisfies ``predicate(node, control, action, eventInfo)``, or
    *total_timeout* elapses.

    For a control where more than one distinct outcome is possible --
    e.g. Zigbee node removal, where the hub reports either an actual
    removal (``_3``/``"NR"``) or a silent fallback to merely disabling the
    node (``_3``/``"EN"`` with ``eventInfo == {"enabled": "false"}``) -- and
    the caller needs to know *which* outcome happened, not just that
    *some* event arrived. Every event on this control while waiting is
    checked directly against *predicate*; a non-matching event (e.g. an
    unrelated action on the same control) is discarded and waiting
    continues, unlike ``wait_until`` there's no separately re-checked
    condition.

    Returns the full ``(node, control, action, eventInfo)`` tuple on a
    match, ``None`` on timeout. Always unregisters before returning.
    """
    waiter = _RegisteredWaiter(nucore_interface, control, None)
    deadline = time.monotonic() + total_timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                item = await asyncio.to_thread(waiter._queue.get, timeout=remaining)
            except queue.Empty:
                return None
            if predicate(*item):
                return item
    finally:
        nucore_interface.unregister_listener(waiter._listener_id, control, None)


async def wait_for_node_event(
    nucore_interface: NuCoreInterface,
    control: str,
    action: str | None,
    total_timeout: float,
) -> str | None:
    """Like ``wait_for_event``, but returns the ``node`` address carried
    directly in the matched event instead of a bare ``True``/``False`` --
    for events whose whole payload *is* the answer (e.g. ``_3``/``NR``'s own
    ``node`` field is the address of the device that was just removed), so
    the caller doesn't need a before/after snapshot diff or a follow-up
    ``_has_address`` re-check to find out what happened.

    Returns the event's ``node`` value if one arrived before *total_timeout*
    elapsed, ``None`` on timeout. Always unregisters before returning.

    Only safe to call *after* the action that triggers the event has left a
    human-scale gap before the event can fire (e.g. a physical pairing/
    exclusion button press) -- see ``wait_for_node_event_around`` for an
    action that can complete essentially instantly on its own (a plain REST
    call the hub might act on, and dispatch the resulting event for, before
    a listener registered only afterward would ever see it).
    """
    waiter = _RegisteredWaiter(nucore_interface, control, action)
    try:
        node, _control, _action, _event_info = await asyncio.to_thread(waiter._queue.get, timeout=total_timeout)
        return node
    except queue.Empty:
        return None
    finally:
        nucore_interface.unregister_listener(waiter._listener_id, control, action)


async def wait_for_node_event_around(
    nucore_interface: NuCoreInterface,
    control: str,
    action: str | None,
    total_timeout: float,
    trigger: Callable[[], Awaitable[bool]],
) -> tuple[bool, str | None]:
    """Like ``wait_for_node_event``, but registers its listener *before*
    awaiting *trigger()* -- the action whose effect the event reports --
    instead of after.

    ``wait_for_node_event`` alone is only safe when the triggering action
    leaves a human-scale gap before the event can possibly fire (a physical
    button press). A plain REST call has no such guarantee: the hub can
    act on it, and fully dispatch the resulting event to every listener
    registered *at that moment*, before the caller's own ``await`` on that
    REST call even returns. There is no event replay/buffer, so a listener
    registered afterward would miss it forever -- registering first closes
    that race.

    Returns ``(trigger_ok, node_address)``: *trigger_ok* is whatever
    *trigger()* itself returned; if it's falsy, the listener is unregistered
    immediately without waiting and *node_address* is ``None``. Otherwise
    *node_address* is the matched event's ``node`` field, or ``None`` if
    *total_timeout* elapses first. Always unregisters before returning.
    """
    waiter = _RegisteredWaiter(nucore_interface, control, action)
    try:
        trigger_ok = await trigger()
        if not trigger_ok:
            return False, None
        node, _control, _action, _event_info = await asyncio.to_thread(waiter._queue.get, timeout=total_timeout)
        return True, node
    except queue.Empty:
        return True, None
    finally:
        nucore_interface.unregister_listener(waiter._listener_id, control, action)

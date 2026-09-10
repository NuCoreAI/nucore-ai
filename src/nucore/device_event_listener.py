"""Thread-based device-event listener.

A listener is a real ``threading.Thread``, not an asyncio primitive, so the
websocket subscriber -- which runs on its own background thread with its own
event loop (see ``NuCoreInterface.subscribe_events``) -- can hand events to it
via a plain, thread-safe method call (``notify``) with no cross-loop bridging
(``call_soon_threadsafe``/``run_coroutine_threadsafe``) anywhere. Registration
and self-unregistration are handled by this base class; subclasses only
implement ``process()``.
"""

from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .nucore_interface import NuCoreInterface


class DeviceEventListener(threading.Thread):
    """Base class for a thread-native device-event listener.

    Registers itself with *nucore_interface* at construction time (safe --
    the internal queue already exists by then, so a ``notify()`` racing in
    immediately after registration has somewhere to land even before
    ``start()`` is called). Does **not** call ``start()`` itself -- that
    stays an explicit call by whoever constructs the listener, to avoid a
    subclass-attribute-not-set-yet race if the base class auto-started
    before a subclass's own ``__init__`` finished.

    Subclasses implement ``process()`` -- when it returns, the thread ends
    and self-unregisters via ``run()``'s ``finally``. A subclass that wants
    to stay alive indefinitely (e.g. a persistent listener) simply never
    returns from ``process()``.
    """

    def __init__(
        self,
        nucore_interface: "NuCoreInterface",
        control: str,
        action: str | None = None,
        listener_id: str | None = None,
    ) -> None:
        super().__init__(daemon=True)
        self.control = control
        self.action = action  # None == wildcard, matches any action for control
        self.result: Any = None
        self._queue: queue.Queue = queue.Queue()
        self._nucore_interface = nucore_interface
        self._listener_id = listener_id or uuid.uuid4().hex
        nucore_interface.register_listener(self._listener_id, control, action, self)

    def notify(self, node, control, action, eventInfo) -> None:
        """Called from the subscriber thread (via
        ``NuCoreInterface._dispatch_event_listeners``) whenever a matching
        event arrives. Must stay fast -- ``queue.Queue.put`` is thread-safe
        and effectively O(1); no other work should happen here."""
        self._queue.put((node, control, action, eventInfo))

    def run(self) -> None:
        try:
            self.result = self.process()
        finally:
            self._nucore_interface.unregister_listener(self._listener_id, self.control, self.action)

    def process(self) -> Any:
        """Subclasses implement this. Owns its own lifecycle and return
        value -- when it returns, the thread ends and self-unregisters."""
        raise NotImplementedError("Subclasses must implement process().")

    def collect_events(
        self,
        first_timeout: float,
        second_timeout: float,
        total_timeout: float,
    ) -> list[tuple] | None:
        """Reusable two-phase burst collector for subclasses to call from
        their own ``process()``.

        Waits up to *first_timeout* for the first matching event -- returns
        ``None`` if nothing arrives (a real timeout). Once one arrives,
        keeps draining with a fresh *second_timeout* per item to catch a
        burst of related events (e.g. a multi-node device's sibling nodes
        appearing in quick succession), bounded by an overall
        *total_timeout* deadline rather than a retry count -- no artificial
        cap on how many items a legitimate burst can contain, but a hard
        ceiling on total wall-clock time so a misbehaving/flapping event
        source can't block the caller forever.
        """
        deadline = time.monotonic() + total_timeout
        try:
            item = self._queue.get(timeout=first_timeout)
        except queue.Empty:
            return None

        result = [item]
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                item = self._queue.get(timeout=min(second_timeout, remaining))
            except queue.Empty:
                break
            result.append(item)

        return result

"""DeviceEventListener -- the thread-based listener base class: notify()/
queue handoff, run()'s result-capture + self-unregister, and the
collect_events() two-phase burst collector.
"""

from __future__ import annotations

import threading
import time

import pytest

from nucore.device_event_listener import DeviceEventListener

# Both tests below intentionally raise inside process(), running on a real
# background thread -- pytest's thread-exception hook reports that as a
# warning by default even though it's the exact scenario under test
# (self-unregistration must happen even when process() raises).
_ignore_thread_exception = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning"
)


class _FakeInterface:
    """Lightweight stand-in for NuCoreInterface -- only the two methods
    DeviceEventListener actually calls, recording invocations."""

    def __init__(self):
        self.registered: list[tuple] = []
        self.unregistered: list[tuple] = []

    def register_listener(self, listener_id, control, action, listener):
        self.registered.append((listener_id, control, action, listener))

    def unregister_listener(self, listener_id, control, action):
        self.unregistered.append((listener_id, control, action))


class _ReturnValueListener(DeviceEventListener):
    """process() returns immediately with a fixed value."""

    def __init__(self, nucore_interface, control, action, value, listener_id=None):
        super().__init__(nucore_interface, control, action, listener_id=listener_id)
        self._value = value

    def process(self):
        return self._value


class _RaisingListener(DeviceEventListener):
    def process(self):
        raise RuntimeError("boom")


def test_init_registers_with_the_interface():
    fake = _FakeInterface()
    listener = _ReturnValueListener(fake, "_3", "ND", value=None)

    assert len(fake.registered) == 1
    listener_id, control, action, registered_listener = fake.registered[0]
    assert control == "_3"
    assert action == "ND"
    assert registered_listener is listener
    assert listener_id  # auto-generated, non-empty


def test_init_uses_given_listener_id_when_provided():
    fake = _FakeInterface()
    listener = _ReturnValueListener(fake, "_3", "NR", value=None, listener_id="my-id")

    assert listener._listener_id == "my-id"
    assert fake.registered[0][0] == "my-id"


def test_auto_generated_listener_ids_differ_across_instances():
    fake = _FakeInterface()
    listener1 = _ReturnValueListener(fake, "_3", "NR", value=None)
    listener2 = _ReturnValueListener(fake, "_3", "NR", value=None)

    assert listener1._listener_id != listener2._listener_id


def test_notify_puts_item_on_queue_before_start():
    fake = _FakeInterface()
    listener = _ReturnValueListener(fake, "_3", "ND", value=None)

    listener.notify("n001", "_3", "ND", {"info": 1})

    item = listener._queue.get_nowait()
    assert item == ("n001", "_3", "ND", {"info": 1})


def test_run_captures_process_return_value_and_self_unregisters():
    fake = _FakeInterface()
    listener = _ReturnValueListener(fake, "_3", "ND", value="matched")

    listener.start()
    listener.join(timeout=2)

    assert listener.result == "matched"
    assert fake.unregistered == [(listener._listener_id, "_3", "ND")]


@_ignore_thread_exception
def test_run_self_unregisters_even_if_process_raises():
    fake = _FakeInterface()
    listener = _RaisingListener(fake, "_3", "NR")

    listener.start()
    listener.join(timeout=2)

    assert listener.result is None  # never assigned -- process() raised
    assert fake.unregistered == [(listener._listener_id, "_3", "NR")]


@_ignore_thread_exception
def test_process_not_overridden_raises_and_still_unregisters():
    fake = _FakeInterface()
    listener = DeviceEventListener(fake, "_3", None)

    listener.start()
    listener.join(timeout=2)

    assert fake.unregistered == [(listener._listener_id, "_3", None)]


# ---------------------------------------------------------------------------
# collect_events -- exercised directly (it's a plain method; queue.Queue is
# thread-safe regardless of which thread put()/get() runs on, so most of
# these don't need a real background thread).
# ---------------------------------------------------------------------------

def test_collect_events_returns_none_on_first_timeout():
    fake = _FakeInterface()
    listener = _ReturnValueListener(fake, "_3", "ND", value=None)

    result = listener.collect_events(first_timeout=0.05, second_timeout=0.05, total_timeout=1.0)

    assert result is None


def test_collect_events_returns_partial_result_when_second_phase_times_out():
    fake = _FakeInterface()
    listener = _ReturnValueListener(fake, "_3", "ND", value=None)
    listener.notify("n001", "_3", "ND", None)

    result = listener.collect_events(first_timeout=1.0, second_timeout=0.05, total_timeout=1.0)

    assert result == [("n001", "_3", "ND", None)]


def test_collect_events_collects_a_burst_already_queued():
    fake = _FakeInterface()
    listener = _ReturnValueListener(fake, "_3", "ND", value=None)
    listener.notify("parent", "_3", "ND", None)
    listener.notify("child1", "_3", "ND", None)
    listener.notify("child2", "_3", "ND", None)

    result = listener.collect_events(first_timeout=1.0, second_timeout=0.2, total_timeout=1.0)

    assert [item[0] for item in result] == ["parent", "child1", "child2"]


def test_collect_events_respects_total_timeout_despite_continuous_items():
    fake = _FakeInterface()
    listener = _ReturnValueListener(fake, "_3", "ND", value=None)
    stop = threading.Event()

    def feeder():
        n = 0
        while not stop.is_set():
            listener.notify(f"n{n}", "_3", "ND", None)
            n += 1
            time.sleep(0.02)

    feeder_thread = threading.Thread(target=feeder, daemon=True)
    feeder_thread.start()

    start = time.monotonic()
    # second_timeout is long enough that individual gets never time out on
    # their own (items keep arriving every 0.02s) -- only total_timeout
    # should end the collection.
    result = listener.collect_events(first_timeout=1.0, second_timeout=1.0, total_timeout=0.2)
    elapsed = time.monotonic() - start

    stop.set()
    feeder_thread.join(timeout=2)

    assert result is not None and len(result) >= 1
    assert elapsed < 0.5  # bounded by total_timeout (0.2s) + generous scheduling slack
    assert len(result) < 40  # did not keep collecting for anywhere near the feeder's full run

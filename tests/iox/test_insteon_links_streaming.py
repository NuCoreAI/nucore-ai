"""INSTEONDiagnostics's event-driven links-table streaming --
``_stream_links_into_file`` (the register-then-drain helper backing
``_get_dev_links_table``/``_get_iox_links_table``/``_get_all_plm_links``)
and those three fetch methods' own wiring around it.

Mirrors the patterns in ``tests/nucore/test_event_listener_registry.py`` and
``tests/unified/handlers/test_event_wait.py``: a minimal concrete
``NuCoreInterface`` subclass standing in for a real ``IoXWrapper``, and
firing events via ``_dispatch_event_listeners`` directly (no real
websocket/hardware involved).

Design note this file exercises: the triggering POST (e.g. `links/device`)
blocks on the real backend until the *entire* scan completes, and its own
result is immaterial -- it is fired as a background task and never awaited,
raced, or otherwise consulted. The event stream (ending in `end_of_table`,
or not, within a rolling `max_gap_timeout` inactivity window) is the sole
source of truth for both timing and success/failure.

Covers:
- ``_stream_links_into_file`` stopping early on a real ``end_of_table``
  record or a "system no longer busy" (control "_5", action "0") event,
  timing out with zero/partial records, exact-action isolation, not giving
  up while events keep arriving within the gap window, and trigger's own
  result/exceptions/pending-ness having zero effect on any of that.
- The three fetch methods: registering before the triggering POST (catching
  even a synchronously-dispatched event), surviving a trigger that raises,
  and degrading gracefully (no exception, valid fenced output) when the
  drain itself times out.
- ``_get_all_plm_links``'s cache-hit path never touching the streaming
  machinery at all.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

import pytest

from nucore.nucore_interface import NuCoreInterface
from iox.diagnostics import insteon_diag as insteon_diag_module
from iox.diagnostics.insteon_diag import (
    INSTEONDiagnostics,
    LINKS_TABLE_FENCE_CLOSE,
    LINKS_TABLE_FENCE_OPEN,
    _parse_links_csv,
)


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        if self._json_data is None:
            raise ValueError("no json body")
        return self._json_data


class FakeIoXWrapper(NuCoreInterface):
    """Minimal concrete NuCoreInterface standing in for a real IoXWrapper --
    only the extra surface INSTEONDiagnostics actually calls (post,
    _family_api_path, get_device_name, _get_group_by_device_group_id) gets a
    real (stubbed) implementation; everything else is an unexercised
    NotImplementedError stub, same as the FakeBackend classes in
    test_event_wait.py/test_event_listener_registry.py."""

    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.plm_connected = True
        # Optional async hook: called with the trigger POST's path whenever
        # a non-"plm-info" POST happens -- lets a test dispatch events
        # synchronously from inside the trigger, or raise, to prove the
        # trigger's own behavior has no bearing on draining.
        self.trigger_hook = None
        self.trigger_post_calls = 0

    async def _load(self, **kwargs): raise NotImplementedError
    async def _load_routines(self): raise NotImplementedError
    async def _load_variables(self): raise NotImplementedError
    async def send_commands(self, commands): raise NotImplementedError
    async def create_automation_routine(self, routine): raise NotImplementedError
    async def update_routine(self, routine): raise NotImplementedError
    async def get_properties(self, device_id): raise NotImplementedError
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

    async def post(self, path, body, headers=None):
        if path.endswith("plm-info"):
            data = "11 22 33 / Connected" if self.plm_connected else "11 22 33 / Disconnected"
            return _FakeResponse(200, json_data={"data": data})
        self.trigger_post_calls += 1
        if self.trigger_hook is not None:
            await self.trigger_hook(path)
        return _FakeResponse(200)

    def _family_api_path(self, suffix, family=None, instance=None):
        return suffix

    def get_device_name(self, device_id):
        return None

    def _get_group_by_device_group_id(self, device_group_id):
        return None


def _responder_event(ix=1):
    return {"ix": ix, "fl": 0xA2, "gr": 6, "id": 0x51AC8D, "data": 0x022C44}


def _end_of_table_event(ix=99):
    return {"ix": ix, "fl": 0, "gr": 0, "id": 0, "data": 0}


def _patch_file_path(monkeypatch, diag, tmp_path):
    monkeypatch.setattr(
        diag, "_get_file_path", lambda type_, device_id: str(tmp_path / f"{type_}_{device_id}.txt")
    )


# ---------------------------------------------------------------------------
# _stream_links_into_file
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_stops_early_on_end_of_table(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "device.txt")

    async def trigger():
        return None

    async def fire_records():
        await asyncio.sleep(0.01)
        wrapper._dispatch_event_listeners("_2", "2", "dev1", _responder_event())
        await asyncio.sleep(0.01)
        wrapper._dispatch_event_listeners("_2", "2", "dev1", _end_of_table_event())

    asyncio.create_task(fire_records())

    start = time.monotonic()
    completed = await diag._stream_links_into_file("2", file_path, "device", trigger, max_gap_timeout=5)
    elapsed = time.monotonic() - start

    assert completed is True
    assert elapsed < 1.0  # ended on the sentinel, not the 5s gap ceiling
    lines = [line for line in open(file_path).read().splitlines() if line]
    assert len(lines) == 2
    assert lines[0].split(",")[1] == "responder"
    assert lines[1].split(",")[1] == "end_of_table"
    assert wrapper._event_listeners == {}


@pytest.mark.asyncio
async def test_stream_timeout_with_zero_records(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "device.txt")

    async def trigger():
        return None

    completed = await diag._stream_links_into_file("2", file_path, "device", trigger, max_gap_timeout=0.05)

    assert completed is False
    assert not os.path.exists(file_path)
    assert wrapper._event_listeners == {}


@pytest.mark.asyncio
async def test_stream_timeout_with_partial_records(tmp_path):
    # One record arrives, then nothing -- the gap after it exceeds
    # max_gap_timeout, so the drain gives up with a partial file.
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "device.txt")

    async def trigger():
        return None

    async def fire_one():
        await asyncio.sleep(0.01)
        wrapper._dispatch_event_listeners("_2", "2", "dev1", _responder_event())

    asyncio.create_task(fire_one())

    completed = await diag._stream_links_into_file("2", file_path, "device", trigger, max_gap_timeout=0.15)

    assert completed is False
    lines = [line for line in open(file_path).read().splitlines() if line]
    assert len(lines) == 1
    assert lines[0].split(",")[1] == "responder"


@pytest.mark.asyncio
async def test_stream_does_not_give_up_while_events_keep_arriving_within_the_gap(tmp_path):
    # Regression test: 3 records spaced well apart (each gap comfortably
    # under max_gap_timeout) followed by end_of_table must all be
    # collected -- a from-start ceiling would have force-stopped a
    # slow-but-steady stream like this, even with events still arriving on
    # schedule.
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "device.txt")

    async def trigger():
        return None

    async def fire_slowly_but_steadily():
        for i in range(3):
            await asyncio.sleep(0.05)
            wrapper._dispatch_event_listeners("_2", "2", "dev1", _responder_event(ix=i))
        await asyncio.sleep(0.05)
        wrapper._dispatch_event_listeners("_2", "2", "dev1", _end_of_table_event())

    asyncio.create_task(fire_slowly_but_steadily())

    # Total elapsed time (~0.2s) comfortably exceeds this single gap
    # timeout (0.15s) -- only a per-event rolling timeout, not a from-start
    # ceiling, lets this complete.
    completed = await diag._stream_links_into_file("2", file_path, "device", trigger, max_gap_timeout=0.15)

    assert completed is True
    lines = [line for line in open(file_path).read().splitlines() if line]
    assert len(lines) == 4
    assert lines[-1].split(",")[1] == "end_of_table"


@pytest.mark.asyncio
async def test_stream_stops_early_on_system_no_longer_busy(tmp_path):
    # Some scans (e.g. an empty table) never emit an end_of_table row at
    # all -- the hub's own "system no longer busy" signal (control "_5",
    # action "0") is a second, independent completion signal.
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "device.txt")

    async def trigger():
        return None

    async def fire_not_busy():
        await asyncio.sleep(0.01)
        wrapper._dispatch_event_listeners("_2", "2", "dev1", _responder_event())
        await asyncio.sleep(0.01)
        wrapper._dispatch_event_listeners("_5", "0", "dev1", {})

    asyncio.create_task(fire_not_busy())

    start = time.monotonic()
    completed = await diag._stream_links_into_file("2", file_path, "device", trigger, max_gap_timeout=5)
    elapsed = time.monotonic() - start

    assert completed is True
    assert elapsed < 1.0  # ended on the not-busy signal, not the 5s gap ceiling
    lines = [line for line in open(file_path).read().splitlines() if line]
    assert len(lines) == 1  # only the responder record -- "_5" isn't a links-table row
    assert wrapper._event_listeners == {}


@pytest.mark.asyncio
async def test_stream_ignores_system_busy_action(tmp_path):
    # Only action "0" (DEVINTIX_SYSTEM_IS_NOT_BUSY_ACTION) is a stop
    # condition -- action "1" (system became busy) must not end the drain.
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "device.txt")

    async def trigger():
        return None

    async def fire_busy_then_end():
        await asyncio.sleep(0.01)
        wrapper._dispatch_event_listeners("_5", "1", "dev1", {})
        await asyncio.sleep(0.01)
        wrapper._dispatch_event_listeners("_2", "2", "dev1", _end_of_table_event())

    asyncio.create_task(fire_busy_then_end())

    completed = await diag._stream_links_into_file("2", file_path, "device", trigger, max_gap_timeout=1)

    assert completed is True
    lines = [line for line in open(file_path).read().splitlines() if line]
    assert len(lines) == 1
    assert lines[0].split(",")[1] == "end_of_table"


@pytest.mark.asyncio
async def test_stream_ignores_events_for_a_different_action(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "device.txt")

    async def trigger():
        return None

    async def fire_wrong_action():
        await asyncio.sleep(0.01)
        # action "3" (iox-table) while draining for action "2" (device-table)
        wrapper._dispatch_event_listeners("_2", "3", "dev1", _responder_event())

    asyncio.create_task(fire_wrong_action())

    completed = await diag._stream_links_into_file("2", file_path, "device", trigger, max_gap_timeout=0.1)

    assert completed is False
    assert not os.path.exists(file_path)


@pytest.mark.asyncio
async def test_stream_ignores_trigger_result_and_exceptions_entirely(tmp_path):
    # The triggering POST's own outcome is immaterial: whether it returns
    # False, raises, or anything else, draining proceeds purely off the
    # event stream. Confirmed via a trigger that raises immediately.
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "device.txt")

    async def trigger():
        raise RuntimeError("network exploded")

    async def fire_records():
        await asyncio.sleep(0.01)
        wrapper._dispatch_event_listeners("_2", "2", "dev1", _end_of_table_event())

    asyncio.create_task(fire_records())

    completed = await diag._stream_links_into_file("2", file_path, "device", trigger, max_gap_timeout=2)

    assert completed is True  # trigger's exception never propagated or affected the outcome
    assert wrapper._event_listeners == {}


@pytest.mark.asyncio
async def test_stream_completes_via_end_of_table_even_though_trigger_is_still_pending(tmp_path):
    # Regression test for the "trigger blocks until the whole scan
    # completes" bug: the triggering POST can still be in flight when
    # end_of_table arrives over the event stream. Draining must not wait on
    # trigger at all, and must NOT cancel trigger_task -- the backend's
    # HTTP call is left to end and return on its own.
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "device.txt")
    never_set = asyncio.Event()
    trigger_task_ref: dict = {}

    async def trigger():
        trigger_task_ref["task"] = asyncio.current_task()
        await never_set.wait()

    async def fire_records():
        await asyncio.sleep(0.01)
        wrapper._dispatch_event_listeners("_2", "2", "dev1", _responder_event())
        await asyncio.sleep(0.01)
        wrapper._dispatch_event_listeners("_2", "2", "dev1", _end_of_table_event())

    asyncio.create_task(fire_records())

    start = time.monotonic()
    completed = await diag._stream_links_into_file("2", file_path, "device", trigger, max_gap_timeout=5)
    elapsed = time.monotonic() - start

    assert completed is True
    assert elapsed < 1.0  # did not wait for trigger or the 5s gap ceiling

    trigger_task = trigger_task_ref["task"]
    assert trigger_task.cancelled() is False
    assert not trigger_task.done()  # left running, per "let it end on its own"
    never_set.set()  # let it resolve so this test's own loop tears down cleanly
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_stream_gives_up_when_trigger_is_still_pending_and_no_events_arrive(tmp_path):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    file_path = str(tmp_path / "device.txt")
    never_set = asyncio.Event()
    trigger_task_ref: dict = {}

    async def trigger():
        trigger_task_ref["task"] = asyncio.current_task()
        await never_set.wait()

    start = time.monotonic()
    completed = await diag._stream_links_into_file("2", file_path, "device", trigger, max_gap_timeout=0.1)
    elapsed = time.monotonic() - start

    assert completed is False
    assert elapsed < 1.0  # gave up on the gap timeout, not forever
    assert trigger_task_ref["task"].cancelled() is False  # left running, not cancelled
    never_set.set()
    await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# _get_dev_links_table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_dev_links_table_registers_before_post_and_catches_synchronous_event(tmp_path, monkeypatch):
    # The whole point of registering before the POST: even an event
    # dispatched from *inside* the trigger itself (no gap at all) is caught.
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    _patch_file_path(monkeypatch, diag, tmp_path)

    async def fire_synchronously(path):
        wrapper._dispatch_event_listeners("_2", "2", "dev1", _end_of_table_event())

    wrapper.trigger_hook = fire_synchronously

    result = await diag._get_dev_links_table("dev1")

    assert LINKS_TABLE_FENCE_OPEN in result
    assert LINKS_TABLE_FENCE_CLOSE in result
    rows = _parse_links_csv(result)
    assert len(rows) == 1
    assert rows[0]["role"] == "end_of_table"
    assert wrapper._event_listeners == {}
    assert diag._is_running is False
    assert diag._file_path is None


@pytest.mark.asyncio
async def test_get_dev_links_table_survives_a_raising_trigger(tmp_path, monkeypatch):
    # The trigger POST's own outcome is immaterial -- even a hard exception
    # from it must not crash the fetch method or affect a result that the
    # event stream itself completed successfully.
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    _patch_file_path(monkeypatch, diag, tmp_path)

    async def fire_then_raise(path):
        wrapper._dispatch_event_listeners("_2", "2", "dev1", _end_of_table_event())
        raise RuntimeError("network exploded")

    wrapper.trigger_hook = fire_then_raise

    result = await diag._get_dev_links_table("dev1")

    rows = _parse_links_csv(result)
    assert len(rows) == 1
    assert rows[0]["role"] == "end_of_table"
    assert diag._is_running is False
    assert diag._file_path is None


@pytest.mark.asyncio
async def test_get_dev_links_table_degrades_gracefully_on_drain_timeout(tmp_path, monkeypatch, caplog):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    _patch_file_path(monkeypatch, diag, tmp_path)

    async def fake_stream(*args, **kwargs):
        return False  # drain never saw end_of_table

    monkeypatch.setattr(diag, "_stream_links_into_file", fake_stream)

    with caplog.at_level(logging.WARNING):
        result = await diag._get_dev_links_table("dev1")

    assert LINKS_TABLE_FENCE_OPEN in result
    assert LINKS_TABLE_FENCE_CLOSE in result
    assert _parse_links_csv(result) == []  # no exception on an empty table
    assert any("timed out" in record.message for record in caplog.records)


# ---------------------------------------------------------------------------
# _get_iox_links_table / _get_all_plm_links -- thin repeats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_iox_links_table_registers_before_post_and_catches_synchronous_event(tmp_path, monkeypatch):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    _patch_file_path(monkeypatch, diag, tmp_path)

    async def fire_synchronously(path):
        wrapper._dispatch_event_listeners("_2", "3", "dev1", _end_of_table_event())

    wrapper.trigger_hook = fire_synchronously

    result = await diag._get_iox_links_table("dev1")

    rows = _parse_links_csv(result)
    assert len(rows) == 1
    assert rows[0]["role"] == "end_of_table"
    assert wrapper._event_listeners == {}


@pytest.mark.asyncio
async def test_get_all_plm_links_registers_before_post_and_catches_synchronous_event(tmp_path, monkeypatch):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    _patch_file_path(monkeypatch, diag, tmp_path)
    monkeypatch.setattr(insteon_diag_module, "_is_cache_fresh", lambda *a, **k: False)

    async def fire_synchronously(path):
        wrapper._dispatch_event_listeners("_2", "1", None, _end_of_table_event())

    wrapper.trigger_hook = fire_synchronously

    result = await diag._get_all_plm_links()

    rows = _parse_links_csv(result)
    assert len(rows) == 1
    assert rows[0]["role"] == "end_of_table"
    assert wrapper._event_listeners == {}


@pytest.mark.asyncio
async def test_get_all_plm_links_cache_hit_never_touches_streaming(tmp_path, monkeypatch):
    wrapper = FakeIoXWrapper()
    diag = INSTEONDiagnostics(wrapper)
    _patch_file_path(monkeypatch, diag, tmp_path)
    # Force a cache hit without needing a real 5000-byte-plus fresh file on
    # disk -- _is_cache_fresh's own size/age logic isn't what's under test
    # here.
    monkeypatch.setattr(insteon_diag_module, "_is_cache_fresh", lambda *a, **k: True)

    result = await diag._get_all_plm_links()

    assert isinstance(result, str)  # _read_from_file's return, even if empty
    assert wrapper.trigger_post_calls == 0  # the streaming trigger POST was never made
    assert wrapper._event_listeners == {}
    assert diag._is_running is False  # never flipped True -- cache path returns before that

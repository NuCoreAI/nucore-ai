"""IoXDiagnostics._begin_plm_op/_end_plm_op -- the four PLM-touching tools
(get_dev_links_table, compare_device_links, get_all_plm_links,
quick_plm_sanity_check) share one hardware PLM connection and must refuse a
second call immediately (no locking/waiting/queueing) while one is already
in flight, regardless of which one. Free-standing tools
(get_full_system_config/get_device_family/get_iox_links_table) are
unaffected either way.

Plan's pair_device joins this *same* lock (via NuCoreInterface.begin_plm_op/
end_plm_op -> IoXWrapper -> IoXDiagnostics._begin_plm_op/_end_plm_op, not a
second, independent one) -- the cross-subsystem tests at the bottom of this
file prove that directly: a diagnostics PLM tool in flight refuses a
concurrent pair_device, and vice versa, because they contend for the exact
same _plm_op_state, not two mutexes that happen to both say "busy".
"""

from __future__ import annotations

import asyncio
import time

import pytest

from iox.diagnostics.iox_diagnostics import IoXDiagnostics
from iox.iox_wrapper import IoXWrapper
from unified.planning.plan_engine import PlanEngine


class _FakeInsteonDiag:
    """Stub for IoXDiagnostics._insteon_diag -- controllable delay per call
    so concurrency behavior can be observed."""

    def __init__(self):
        self.calls: list[str] = []

    async def _get_dev_links_table(self, device_id, **kwargs):
        self.calls.append("get_dev_links_table")
        return "dev link table"

    async def _get_all_plm_links(self, **kwargs):
        self.calls.append("get_all_plm_links")
        await asyncio.sleep(0.2)
        return "all plm links"

    async def _compare_device_links(self, device_id, **kwargs):
        self.calls.append("compare_device_links")
        return "comparison report"

    async def _quick_plm_sanity_check(self, **kwargs):
        self.calls.append("quick_plm_sanity_check")
        return "sanity report"


def _bare_diagnostics() -> IoXDiagnostics:
    diag = object.__new__(IoXDiagnostics)
    diag._plm_op_state = None
    diag._insteon_diag = _FakeInsteonDiag()
    diag._iox_wrapper = None
    return diag


def _always_insteon(self, device_id=None):
    return True


@pytest.mark.asyncio
async def test_second_plm_call_is_refused_immediately_while_first_is_in_flight(monkeypatch):
    diag = _bare_diagnostics()
    monkeypatch.setattr(IoXDiagnostics, "_init_insteon_diag", _always_insteon)

    start = time.monotonic()
    winner, loser = await asyncio.gather(
        diag.get_all_plm_links(),  # sleeps 0.2s
        diag.compare_device_links(device_id="n001"),
    )
    elapsed = time.monotonic() - start

    # get_all_plm_links started first (gather preserves submission order for
    # which coroutine's first await yields control first) and holds the
    # lock for its whole 0.2s sleep -- compare_device_links must be refused
    # immediately, not queued behind it.
    assert winner == "all plm links"
    assert "error" in loser
    assert "already in progress" in loser["error"]
    # The loser returned immediately -- total time is close to the winner's
    # own 0.2s, not roughly double it (which would mean it waited its turn).
    assert elapsed < 0.35


@pytest.mark.asyncio
async def test_plm_op_state_cleared_after_success(monkeypatch):
    diag = _bare_diagnostics()
    monkeypatch.setattr(IoXDiagnostics, "_init_insteon_diag", _always_insteon)

    await diag.get_dev_links_table(device_id="n001")

    assert diag._plm_op_state is None


@pytest.mark.asyncio
async def test_plm_op_state_cleared_after_exception(monkeypatch):
    diag = _bare_diagnostics()
    monkeypatch.setattr(IoXDiagnostics, "_init_insteon_diag", _always_insteon)

    async def failing(*a, **kw):
        raise RuntimeError("hub unreachable")

    diag._insteon_diag._get_dev_links_table = failing

    with pytest.raises(RuntimeError):
        await diag.get_dev_links_table(device_id="n001")

    assert diag._plm_op_state is None


@pytest.mark.asyncio
async def test_stale_plm_op_state_is_cleared_and_a_new_call_proceeds(monkeypatch):
    diag = _bare_diagnostics()
    monkeypatch.setattr(IoXDiagnostics, "_init_insteon_diag", _always_insteon)
    diag._plm_op_state = {
        "step": "get_all_plm_links",
        "started_at": time.monotonic() - (IoXDiagnostics._PLM_OP_TIMEOUT_S + 5),
    }

    result = await diag.compare_device_links(device_id="n001")

    assert result == "comparison report"
    assert diag._plm_op_state is None


def test_begin_plm_op_refuses_a_second_call_while_first_active():
    diag = _bare_diagnostics()

    first = diag._begin_plm_op("get_dev_links_table")
    second = diag._begin_plm_op("quick_plm_sanity_check")

    assert first is None
    assert "error" in second
    assert "get_dev_links_table" in second["error"]


@pytest.mark.asyncio
async def test_free_standing_tools_are_unaffected_by_an_in_flight_plm_op(monkeypatch):
    diag = _bare_diagnostics()
    monkeypatch.setattr(IoXDiagnostics, "_init_insteon_diag", _always_insteon)
    diag._iox_wrapper = type("W", (), {"_get_node_family": lambda self, device_id: ("2", "insteon")})()

    # Manually mark a PLM op as in-flight, as if get_all_plm_links were
    # mid-call -- get_device_family (free-standing) must still succeed.
    diag._plm_op_state = {"step": "get_all_plm_links", "started_at": time.monotonic()}

    result = await diag.get_device_family(device_id="n001")

    assert result == "insteon"
    # The PLM op state is untouched -- get_device_family never looks at it.
    assert diag._plm_op_state is not None


# ---------------------------------------------------------------------------
# Cross-subsystem: Plan's pair_device shares this exact lock, via
# NuCoreInterface.begin_plm_op/end_plm_op -> IoXWrapper -> IoXDiagnostics.
# ---------------------------------------------------------------------------


def _bare_wrapper(diag: IoXDiagnostics) -> IoXWrapper:
    wrapper = object.__new__(IoXWrapper)
    wrapper.diagnostics = diag
    return wrapper


@pytest.mark.asyncio
async def test_pair_device_is_refused_while_a_diagnostics_plm_tool_is_in_flight(monkeypatch):
    diag = _bare_diagnostics()
    monkeypatch.setattr(IoXDiagnostics, "_init_insteon_diag", _always_insteon)
    wrapper = _bare_wrapper(diag)
    plan_engine = PlanEngine()

    pairing_calls: list[str] = []

    async def fake_add_device(device_address, **kwargs):
        pairing_calls.append(device_address)
        return "added"

    wrapper.add_device = fake_add_device

    start = time.monotonic()
    winner, loser = await asyncio.gather(
        diag.get_all_plm_links(),  # sleeps 0.2s, holds the lock
        plan_engine.pair_device(wrapper, protocol="insteon", device_address="1A 2B 3C 1"),
    )
    elapsed = time.monotonic() - start

    assert winner == "all plm links"
    assert "error" in loser
    assert "already in progress" in loser["error"]
    assert pairing_calls == []  # add_device was never reached -- refused before it, not after
    assert elapsed < 0.35


@pytest.mark.asyncio
async def test_a_diagnostics_plm_tool_is_refused_while_pair_device_is_in_flight(monkeypatch):
    diag = _bare_diagnostics()
    monkeypatch.setattr(IoXDiagnostics, "_init_insteon_diag", _always_insteon)
    wrapper = _bare_wrapper(diag)
    plan_engine = PlanEngine()

    async def slow_add_device(device_address, **kwargs):
        await asyncio.sleep(0.2)
        return "added"

    wrapper.add_device = slow_add_device

    start = time.monotonic()
    winner, loser = await asyncio.gather(
        plan_engine.pair_device(wrapper, protocol="insteon", device_address="1A 2B 3C 1"),
        diag.get_all_plm_links(),
    )
    elapsed = time.monotonic() - start

    assert winner == "added"
    assert "error" in loser
    assert "already in progress" in loser["error"]
    assert elapsed < 0.35

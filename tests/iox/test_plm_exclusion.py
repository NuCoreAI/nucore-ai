"""IoXDiagnostics._begin_plm_op/_end_plm_op -- atomic mutual exclusion
around exactly the 4 standalone tools that drive the shared PLM serial
connection (get_dev_links_table, compare_device_links, get_all_plm_links,
quick_plm_sanity_check). A second call to any of the four while one is
already in flight (including a second call to the same one) is refused
immediately -- no locking/waiting, no queueing.
"""

from __future__ import annotations

import asyncio

import pytest

from iox.diagnostics.iox_diagnostics import IoXDiagnostics


class _FakeInsteonDiag:
    """Stands in for INSTEONDiagnostics -- each method sleeps briefly so
    asyncio.gather's scheduling (the first coroutine runs to its first await,
    yielding control, before the second starts) can prove the second caller
    is refused immediately rather than queued behind the first."""

    def __init__(self):
        self.calls: list[str] = []

    async def _get_dev_links_table(self, device_id=None, **kwargs):
        self.calls.append("get_dev_links_table")
        await asyncio.sleep(0.05)
        return "dev links"

    async def _get_iox_links_table(self, device_id=None, **kwargs):
        self.calls.append("get_iox_links_table")
        return "iox links"

    async def _compare_device_links(self, device_id=None, **kwargs):
        self.calls.append("compare_device_links")
        await asyncio.sleep(0.05)
        return "compare result"

    async def _get_all_plm_links(self, **kwargs):
        self.calls.append("get_all_plm_links")
        await asyncio.sleep(0.05)
        return "all plm links"

    async def _quick_plm_sanity_check(self, **kwargs):
        self.calls.append("quick_plm_sanity_check")
        await asyncio.sleep(0.05)
        return "sanity ok"


def _bare_diagnostics_for_plm_lock() -> IoXDiagnostics:
    diag = object.__new__(IoXDiagnostics)
    diag._plm_op_state = None
    diag._insteon_diag = _FakeInsteonDiag()
    diag._init_insteon_diag = lambda device_id=None, **kwargs: True
    return diag


_PLM_METHODS = {
    "get_dev_links_table": lambda diag: diag.get_dev_links_table(device_id="n001"),
    "compare_device_links": lambda diag: diag.compare_device_links(device_id="n001"),
    "get_all_plm_links": lambda diag: diag.get_all_plm_links(),
    "quick_plm_sanity_check": lambda diag: diag.quick_plm_sanity_check(),
}


async def _fake_get_system_options():
    return {"INSTEONSupport": True}


async def _fake_get_core_services_status():
    return "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("first_name,second_name", [(a, b) for a in _PLM_METHODS for b in _PLM_METHODS])
async def test_a_plm_tool_refuses_any_concurrent_call_to_any_of_the_four(first_name, second_name):
    # Covers all 16 ordered pairs, including a tool colliding with a second
    # call to itself -- enforced as "any of the 4, period," while one of the
    # 4 is already in flight.
    diag = _bare_diagnostics_for_plm_lock()
    diag._get_system_options = _fake_get_system_options
    diag.get_core_services_status = _fake_get_core_services_status

    first_result, second_result = await asyncio.gather(_PLM_METHODS[first_name](diag), _PLM_METHODS[second_name](diag))

    # Only the winner actually reached the hardware call -- the loser was
    # refused before ever touching _insteon_diag.
    assert diag._insteon_diag.calls == [first_name]
    assert isinstance(second_result, dict) and "error" in second_result
    assert "already in progress" in second_result["error"]
    assert first_name in second_result["error"]
    assert diag._plm_op_state is None  # winner's `finally` already released it


@pytest.mark.asyncio
async def test_plm_lock_clears_after_a_call_completes_allowing_a_subsequent_call():
    diag = _bare_diagnostics_for_plm_lock()

    first = await diag.get_all_plm_links()
    second = await diag.compare_device_links(device_id="n001")

    assert first == "all plm links"
    assert second == "compare result"
    assert diag._plm_op_state is None


@pytest.mark.asyncio
async def test_plm_lock_clears_even_when_the_underlying_call_raises():
    diag = _bare_diagnostics_for_plm_lock()

    async def failing(**kwargs):
        raise RuntimeError("hub unreachable")

    diag._insteon_diag._get_all_plm_links = failing

    with pytest.raises(RuntimeError, match="hub unreachable"):
        await diag.get_all_plm_links()

    assert diag._plm_op_state is None


@pytest.mark.asyncio
async def test_non_plm_tool_runs_freely_while_a_plm_op_is_marked_in_flight():
    diag = _bare_diagnostics_for_plm_lock()
    diag._plm_op_state = {"step": "get_all_plm_links"}  # simulates one of the four mid-call

    result = await diag.get_iox_links_table(device_id="n001")

    assert result == "iox links"  # not refused -- get_iox_links_table isn't one of the four
    assert diag._plm_op_state == {"step": "get_all_plm_links"}  # untouched

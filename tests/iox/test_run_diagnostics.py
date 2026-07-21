"""IoXDiagnostics.get_diagnostics_map/run_diagnostics -- the named-plan
registry (_IOX_DIAGNOSTICS_PLAN_REGISTRY), single-in-flight concurrency lock,
poll-same-plan semantics, the stop plan as the always-allowed interrupt, and
stale-lock timeout clearing. Also covers IoXWrapper's thin delegation to it.
"""

from __future__ import annotations

import time

import pytest

from iox.iox_diagnostics import IoXDiagnostics, STOP_LONG_RUNNING_DIAGNOSTIC
from iox.iox_wrapper import IoXWrapper

NO_FEEDBACK = "No Device Feedback"  # long-running
NO_COMMUNICATION = "No Device Communication"  # long-running
NO_REMOTE = "No Remote Connectivity"  # short, not long-running
STOP = STOP_LONG_RUNNING_DIAGNOSTIC


def _bare_diagnostics() -> IoXDiagnostics:
    diag = object.__new__(IoXDiagnostics)
    diag._diagnostics_state = None
    return diag


def test_get_diagnostics_map_lists_registered_plans_with_long_running_flag():
    diag = _bare_diagnostics()
    entries = {e["name"]: e for e in diag.get_diagnostics_map()}

    assert entries[NO_FEEDBACK]["long_running"] is True
    assert entries[NO_REMOTE]["long_running"] is False
    assert entries[STOP]["long_running"] is False


@pytest.mark.asyncio
async def test_unknown_name_errors():
    diag = _bare_diagnostics()
    result = await diag.run_diagnostics("NOT_A_REAL_PLAN")
    assert "error" in result


@pytest.mark.asyncio
async def test_short_diagnostic_runs_synchronously_and_completes_immediately():
    diag = _bare_diagnostics()
    result = await diag.run_diagnostics(NO_REMOTE)

    assert result["diagnostics"] == NO_REMOTE
    assert result["status"] == "completed"
    assert diag._diagnostics_state is None  # no lock held for short diagnostics


@pytest.mark.asyncio
async def test_long_diagnostic_starts_and_returns_immediately_without_blocking():
    diag = _bare_diagnostics()
    result = await diag.run_diagnostics(NO_FEEDBACK)

    assert result["diagnostics"] == NO_FEEDBACK
    assert result["status"] == "started"
    assert diag._diagnostics_state["name"] == NO_FEEDBACK
    assert diag._diagnostics_state["status"] == "running"


@pytest.mark.asyncio
async def test_second_different_diagnostic_conflicts_while_one_is_running():
    diag = _bare_diagnostics()
    diag._diagnostics_state = {
        "name": NO_FEEDBACK, "started_at": time.monotonic(), "status": "running", "result": None
    }

    result = await diag.run_diagnostics(NO_COMMUNICATION)

    assert "error" in result
    assert NO_FEEDBACK in result["error"]
    # names the stop plan so the caller knows how to unblock it
    assert STOP in result["error"]
    # original lock untouched
    assert diag._diagnostics_state["name"] == NO_FEEDBACK


@pytest.mark.asyncio
async def test_polling_the_same_running_plan_returns_progress_not_conflict():
    diag = _bare_diagnostics()
    diag._diagnostics_state = {
        "name": NO_FEEDBACK, "started_at": time.monotonic(), "status": "running", "result": None
    }

    result = await diag.run_diagnostics(NO_FEEDBACK)

    assert result["status"] == "running"
    assert "elapsed_s" in result
    # still tracked -- polling never clears an in-progress run
    assert diag._diagnostics_state is not None
    assert diag._diagnostics_state["name"] == NO_FEEDBACK


@pytest.mark.asyncio
async def test_polling_a_completed_plan_returns_result_once_then_clears():
    diag = _bare_diagnostics()
    diag._diagnostics_state = {
        "name": NO_FEEDBACK, "started_at": 0.0, "status": "completed", "result": "the result"
    }

    result = await diag.run_diagnostics(NO_FEEDBACK)
    assert result == {"diagnostics": NO_FEEDBACK, "status": "completed", "result": "the result"}
    assert diag._diagnostics_state is None

    # a second poll after it's been cleared starts a genuinely fresh run
    second = await diag.run_diagnostics(NO_FEEDBACK)
    assert second["status"] == "started"


@pytest.mark.asyncio
async def test_stop_plan_is_reachable_and_clears_the_lock():
    diag = _bare_diagnostics()
    diag._diagnostics_state = {
        "name": NO_FEEDBACK, "started_at": time.monotonic(), "status": "running", "result": None
    }

    calls = []

    async def fake_stop():
        calls.append("stopped")
        return "stopped ok"

    diag.stop_long_running_diagnostic = fake_stop

    result = await diag.run_diagnostics(STOP)

    assert result == {"diagnostics": STOP, "status": "stopped", "result": "stopped ok"}
    assert diag._diagnostics_state is None
    assert calls == ["stopped"]


@pytest.mark.asyncio
async def test_stop_plan_works_even_when_nothing_is_running():
    # Regression test: the stop plan must not be routed through the generic
    # candidates-forwarding call path (stop_long_running_diagnostic takes no
    # arguments), and must be reachable when there's no conflicting state at
    # all, not just when clearing an active lock.
    diag = _bare_diagnostics()

    async def fake_stop():
        return "nothing was running"

    diag.stop_long_running_diagnostic = fake_stop

    result = await diag.run_diagnostics(STOP)

    assert result == {"diagnostics": STOP, "status": "stopped", "result": "nothing was running"}


@pytest.mark.asyncio
async def test_stale_running_lock_past_timeout_is_cleared_for_a_different_plan():
    diag = _bare_diagnostics()
    stale_start = time.monotonic() - (diag._DIAGNOSTICS_TIMEOUT_S + 5)
    diag._diagnostics_state = {
        "name": NO_FEEDBACK, "started_at": stale_start, "status": "running", "result": None
    }

    result = await diag.run_diagnostics(NO_COMMUNICATION)

    assert result["status"] == "started"
    assert diag._diagnostics_state["name"] == NO_COMMUNICATION


@pytest.mark.asyncio
async def test_polling_same_plan_past_timeout_reports_timed_out():
    diag = _bare_diagnostics()
    stale_start = time.monotonic() - (diag._DIAGNOSTICS_TIMEOUT_S + 5)
    diag._diagnostics_state = {
        "name": NO_FEEDBACK, "started_at": stale_start, "status": "running", "result": None
    }

    result = await diag.run_diagnostics(NO_FEEDBACK)

    assert result == {"diagnostics": NO_FEEDBACK, "status": "timed_out"}
    assert diag._diagnostics_state is None


@pytest.mark.asyncio
async def test_run_long_diagnostic_records_completion_on_state():
    diag = _bare_diagnostics()

    async def fake_plan(candidates=None, **kwargs):
        return {"ok": True}

    diag._diagnostics_state = {
        "name": NO_FEEDBACK, "started_at": time.monotonic(), "status": "running", "result": None
    }

    await diag._run_long_diagnostic(NO_FEEDBACK, fake_plan, None)

    assert diag._diagnostics_state["status"] == "completed"
    assert diag._diagnostics_state["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_run_long_diagnostic_records_error_on_exception():
    diag = _bare_diagnostics()

    async def failing(candidates=None, **kwargs):
        raise RuntimeError("hub unreachable")

    diag._diagnostics_state = {
        "name": NO_FEEDBACK, "started_at": time.monotonic(), "status": "running", "result": None
    }

    await diag._run_long_diagnostic(NO_FEEDBACK, failing, None)

    assert diag._diagnostics_state["status"] == "error"
    assert "hub unreachable" in diag._diagnostics_state["result"]


def test_get_running_diagnostic_returns_none_when_nothing_active():
    diag = _bare_diagnostics()
    assert diag.get_running_diagnostic() is None


def test_get_running_diagnostic_reports_active_run():
    diag = _bare_diagnostics()
    diag._diagnostics_state = {
        "name": NO_FEEDBACK, "started_at": time.monotonic() - 3, "status": "running", "result": None
    }

    info = diag.get_running_diagnostic()

    assert info["diagnostics"] == NO_FEEDBACK
    assert info["status"] == "running"
    assert info["elapsed_s"] >= 3


def test_get_running_diagnostic_is_none_for_completed_state():
    diag = _bare_diagnostics()
    diag._diagnostics_state = {"name": NO_FEEDBACK, "started_at": 0.0, "status": "completed", "result": "x"}
    assert diag.get_running_diagnostic() is None


def test_get_running_diagnostic_is_none_once_past_timeout():
    diag = _bare_diagnostics()
    stale_start = time.monotonic() - (diag._DIAGNOSTICS_TIMEOUT_S + 5)
    diag._diagnostics_state = {"name": NO_FEEDBACK, "started_at": stale_start, "status": "running", "result": None}
    assert diag.get_running_diagnostic() is None


# ----------------------------------------------------------------------
# candidate_devices/candidate_routines -- carried along as context for what
# a fuzzy diagnostic request concerns, not used to pick which plan runs.
# ----------------------------------------------------------------------

CANDIDATE_DEVICES = [{"device_id": "n001", "score": 0.9}]
CANDIDATE_ROUTINES = [{"routine_id": "r001", "score": 0.8}]


@pytest.mark.asyncio
async def test_short_diagnostic_echoes_candidates_when_provided():
    diag = _bare_diagnostics()
    result = await diag.run_diagnostics(NO_REMOTE, candidate_devices=CANDIDATE_DEVICES)

    assert result["candidates"] == {"devices": CANDIDATE_DEVICES, "routines": []}


@pytest.mark.asyncio
async def test_short_diagnostic_omits_candidates_key_when_none_given():
    diag = _bare_diagnostics()
    result = await diag.run_diagnostics(NO_REMOTE)

    assert "candidates" not in result


@pytest.mark.asyncio
async def test_long_diagnostic_stores_and_echoes_candidates_on_start():
    diag = _bare_diagnostics()
    result = await diag.run_diagnostics(NO_FEEDBACK, candidate_routines=CANDIDATE_ROUTINES)

    assert result["candidates"] == {"devices": [], "routines": CANDIDATE_ROUTINES}
    assert diag._diagnostics_state["candidates"] == {"devices": [], "routines": CANDIDATE_ROUTINES}


@pytest.mark.asyncio
async def test_polling_a_running_diagnostic_echoes_the_original_candidates_not_new_ones():
    diag = _bare_diagnostics()
    diag._diagnostics_state = {
        "name": NO_FEEDBACK,
        "started_at": time.monotonic(),
        "status": "running",
        "result": None,
        "candidates": {"devices": CANDIDATE_DEVICES, "routines": []},
    }

    result = await diag.run_diagnostics(NO_FEEDBACK, candidate_devices=[{"device_id": "different", "score": 1.0}])

    assert result["candidates"] == {"devices": CANDIDATE_DEVICES, "routines": []}


def test_get_running_diagnostic_includes_candidates_when_present():
    diag = _bare_diagnostics()
    diag._diagnostics_state = {
        "name": NO_FEEDBACK,
        "started_at": time.monotonic() - 3,
        "status": "running",
        "result": None,
        "candidates": {"devices": CANDIDATE_DEVICES, "routines": []},
    }

    info = diag.get_running_diagnostic()

    assert info["candidates"] == {"devices": CANDIDATE_DEVICES, "routines": []}


def test_get_running_diagnostic_omits_candidates_key_without_a_missing_key_error():
    # Pre-existing state dicts (constructed before this field existed) have
    # no "candidates" key at all -- must not raise.
    diag = _bare_diagnostics()
    diag._diagnostics_state = {"name": NO_FEEDBACK, "started_at": time.monotonic() - 3, "status": "running", "result": None}

    info = diag.get_running_diagnostic()

    assert "candidates" not in info


# ----------------------------------------------------------------------
# IoXWrapper delegation -- get_diagnostics_map/run_diagnostics/
# get_running_diagnostic must just forward to self.diagnostics.
# ----------------------------------------------------------------------

def _bare_wrapper_with_diagnostics() -> IoXWrapper:
    wrapper = object.__new__(IoXWrapper)
    wrapper.diagnostics = _bare_diagnostics()
    return wrapper


def test_wrapper_get_diagnostics_map_delegates():
    wrapper = _bare_wrapper_with_diagnostics()
    assert wrapper.get_diagnostics_map() == wrapper.diagnostics.get_diagnostics_map()


@pytest.mark.asyncio
async def test_wrapper_run_diagnostics_delegates():
    wrapper = _bare_wrapper_with_diagnostics()
    result = await wrapper.run_diagnostics(NO_REMOTE)
    assert result["diagnostics"] == NO_REMOTE
    assert result["status"] == "completed"


def test_wrapper_get_running_diagnostic_delegates():
    wrapper = _bare_wrapper_with_diagnostics()
    wrapper.diagnostics._diagnostics_state = {
        "name": NO_FEEDBACK, "started_at": time.monotonic() - 3, "status": "running", "result": None
    }
    assert wrapper.get_running_diagnostic() == wrapper.diagnostics.get_running_diagnostic()

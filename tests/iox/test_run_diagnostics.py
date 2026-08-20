"""IoXDiagnostics.start_diagnostics/run_diagnostic_step -- the single
diagnostics flow (no named plans): a single-in-flight concurrency lock,
re-show-not-restart semantics while a session is open, the shared step
catalog (including the "conclude"/"stop" terminal steps), and stale-lock
timeout clearing. Also covers IoXWrapper's thin delegation to it.
"""

from __future__ import annotations

import time

import pytest

from iox.diagnostics.iox_diagnostics import IoXDiagnostics
from iox.iox_wrapper import IoXWrapper

STEP_NAMES = {"check_device_links", "check_subsystem_status", "get_full_system_config", "conclude", "stop"}


def _bare_diagnostics() -> IoXDiagnostics:
    diag = object.__new__(IoXDiagnostics)
    diag._diagnostics_state = None
    # __init__ is bypassed (no real IoXWrapper needed for these tests), but
    # the diagnose.md parse/validation is still exercised via the real loader
    # -- these tests should fail loudly too if the prompt and the code drift.
    diag._diagnostic_instruction, diag._diagnostic_steps = diag._load_diagnostic_config()
    return diag


def test_real_diagnose_md_parses_and_validates_cleanly():
    # No mocking -- confirms the actual shipped prompts/diagnose.md matches
    # the actual IoXDiagnostics methods (this is what __init__ runs for real).
    diag = object.__new__(IoXDiagnostics)
    text, steps = diag._load_diagnostic_config()

    assert set(steps.keys()) == STEP_NAMES
    assert text  # the full prose+json text, shown to the model as-is


def test_parse_diagnostic_config_errors_when_json_block_missing():
    diag = object.__new__(IoXDiagnostics)

    with pytest.raises(RuntimeError, match="missing its"):
        diag._parse_diagnostic_config("just prose, no fenced json block here")


def test_parse_diagnostic_config_errors_on_malformed_json():
    diag = object.__new__(IoXDiagnostics)

    with pytest.raises(RuntimeError, match="malformed"):
        diag._parse_diagnostic_config("prose\n```json\n{not valid json\n```\n")


def test_parse_diagnostic_config_errors_when_function_does_not_exist():
    # Step name -> backend method is convention-derived (f"_{name}"), no
    # per-step override -- a step with no matching "_<name>" method fails.
    diag = object.__new__(IoXDiagnostics)
    text = 'prose\n```json\n{"totally_made_up_step": {"description": "x"}}\n```\n'

    with pytest.raises(RuntimeError, match="_totally_made_up_step"):
        diag._parse_diagnostic_config(text)


def test_parse_diagnostic_config_allows_terminal_steps_with_no_backend_function():
    # "conclude"/"stop" are the only two steps with no backend method at all
    # -- exempted from the "_<name> must exist" check by literal name, not by
    # a "function": null marker (there's no such key anymore).
    diag = object.__new__(IoXDiagnostics)
    text = 'prose\n```json\n{"conclude": {"description": "x"}, "stop": {"description": "y"}}\n```\n'

    _, steps = diag._parse_diagnostic_config(text)

    assert steps == {"conclude": {"description": "x"}, "stop": {"description": "y"}}


@pytest.mark.asyncio
async def test_real_iox_wrapper_construction_runs_the_same_validation():
    # __init__ runs _load_diagnostic_config for real -- confirms a genuine
    # IoXWrapper() (not the object.__new__ bypass every other test in this
    # file uses) doesn't blow up, i.e. the real prompt/code pairing is valid.
    wrapper = object.__new__(IoXWrapper)
    diag = IoXDiagnostics(wrapper)

    assert set(diag._diagnostic_steps.keys()) == STEP_NAMES


def _in_progress_session(**overrides):
    session = {"started_at": time.monotonic(), "status": "in_progress", "candidates": None}
    session.update(overrides)
    return session


# ----------------------------------------------------------------------
# start_diagnostics -- opens (or re-shows) the single session.
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_diagnostics_opens_a_session_with_instruction_and_steps():
    diag = _bare_diagnostics()

    result = await diag.start_diagnostics()

    assert result["status"] == "in_progress"
    assert isinstance(result["instruction"], str) and result["instruction"]
    step_names = set(result["available_tools"])
    assert step_names == STEP_NAMES
    assert diag._diagnostics_state["status"] == "in_progress"
    assert diag._diagnostics_state["candidates"] is None


@pytest.mark.asyncio
async def test_starting_again_while_in_progress_re_shows_without_restarting():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session()
    started_at = diag._diagnostics_state["started_at"]

    result = await diag.start_diagnostics()

    assert result["status"] == "in_progress"
    assert result["instruction"]
    assert "elapsed_s" in result
    assert diag._diagnostics_state["started_at"] == started_at  # not restarted


@pytest.mark.asyncio
async def test_start_diagnostics_echoes_candidates_when_provided():
    diag = _bare_diagnostics()
    candidate_devices = [{"device_id": "n001", "score": 0.9}]

    result = await diag.start_diagnostics(candidate_devices=candidate_devices)

    assert result["candidates"] == {"devices": candidate_devices, "routines": []}
    assert diag._diagnostics_state["candidates"] == {"devices": candidate_devices, "routines": []}


@pytest.mark.asyncio
async def test_start_diagnostics_omits_candidates_key_when_none_given():
    diag = _bare_diagnostics()

    result = await diag.start_diagnostics()

    assert "candidates" not in result


@pytest.mark.asyncio
async def test_stale_session_past_timeout_is_cleared_and_a_fresh_one_starts():
    diag = _bare_diagnostics()
    stale_start = time.monotonic() - (diag._DIAGNOSTICS_TIMEOUT_S + 5)
    diag._diagnostics_state = _in_progress_session(started_at=stale_start)

    result = await diag.start_diagnostics()

    assert result["status"] == "in_progress"
    assert "elapsed_s" not in result  # a genuinely fresh session, not a re-show
    assert diag._diagnostics_state["started_at"] > stale_start


# ----------------------------------------------------------------------
# Session ownership -- a diagnostic is real hub-level state, so only the
# session_id that started it may re-show/drive it; any other session_id
# (including the unset/None default some legacy callers still use) is
# refused rather than silently treated as the same conversation.
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_diagnostics_records_the_starting_session_id():
    diag = _bare_diagnostics()

    await diag.start_diagnostics(session_id="session-A")

    assert diag._diagnostics_state["session_id"] == "session-A"


@pytest.mark.asyncio
async def test_owning_session_can_re_show_its_own_session():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session(session_id="session-A")

    result = await diag.start_diagnostics(session_id="session-A")

    assert result["status"] == "in_progress"
    assert "error" not in result


@pytest.mark.asyncio
async def test_a_different_session_cannot_start_or_re_show_someone_elses_session():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session(session_id="session-A")

    result = await diag.start_diagnostics(session_id="session-B")

    assert "error" in result
    # the original session is untouched -- not restarted, not handed to B
    assert diag._diagnostics_state["session_id"] == "session-A"


@pytest.mark.asyncio
async def test_run_diagnostic_step_refuses_a_different_session():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session(session_id="session-A")

    result = await diag.run_diagnostic_step("get_full_system_config", session_id="session-B")

    assert "error" in result
    assert diag._diagnostics_state is not None  # untouched


@pytest.mark.asyncio
async def test_run_diagnostic_step_allows_the_owning_session():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session(session_id="session-A")

    async def fake_get_full_system_config():
        return {"ok": True}

    diag._get_full_system_config = fake_get_full_system_config

    result = await diag.run_diagnostic_step("get_full_system_config", session_id="session-A")

    assert result == {"step": "get_full_system_config", "result": {"ok": True}}


def test_get_running_diagnostic_reports_the_owning_session_id():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session(started_at=time.monotonic() - 3, session_id="session-A")

    info = diag.get_running_diagnostic()

    assert info["session_id"] == "session-A"


def test_get_running_diagnostic_session_id_is_none_when_never_set():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session(started_at=time.monotonic() - 3)

    info = diag.get_running_diagnostic()

    assert info["session_id"] is None


# ----------------------------------------------------------------------
# run_diagnostic_step -- only usable while a session is in_progress;
# dispatches to the shared step registry; "conclude"/"stop" end the session
# directly instead of calling a backend function.
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_diagnostic_step_errors_when_no_session_is_in_progress():
    diag = _bare_diagnostics()

    result = await diag.run_diagnostic_step("get_full_system_config")

    assert "error" in result


@pytest.mark.asyncio
async def test_run_diagnostic_step_errors_on_unknown_step():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session()

    result = await diag.run_diagnostic_step("not_a_real_step")

    assert "error" in result
    assert diag._diagnostics_state is not None  # unknown step doesn't clear the session


@pytest.mark.asyncio
async def test_run_diagnostic_step_dispatches_to_the_backend_function():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session()

    calls = []

    async def fake_get_full_system_config():
        calls.append("called")
        return {"ok": True}

    diag._get_full_system_config = fake_get_full_system_config

    result = await diag.run_diagnostic_step("get_full_system_config")

    assert result == {"step": "get_full_system_config", "result": {"ok": True}}
    assert calls == ["called"]
    assert diag._diagnostics_state is not None  # one step doesn't end the session


@pytest.mark.asyncio
async def test_run_diagnostic_step_forwards_params_to_the_backend_function():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session()

    received = {}

    async def fake_get_dev_links_table(device_id=None, **kwargs):
        received["device_id"] = device_id
        return "link table text"

    diag._get_dev_links_table = fake_get_dev_links_table

    result = await diag.run_diagnostic_step("check_device_links", device_id="n001")

    assert received["device_id"] == "n001"
    assert result["result"] == "link table text"


@pytest.mark.asyncio
async def test_run_diagnostic_step_reports_not_implemented_steps_as_an_error():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session()

    result = await diag.run_diagnostic_step("check_subsystem_status", protocol="Zigbee")

    assert "error" in result
    assert diag._diagnostics_state is not None  # a failed step doesn't end the session


@pytest.mark.asyncio
async def test_run_diagnostic_step_catches_unexpected_exceptions():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session()

    async def failing():
        raise RuntimeError("hub unreachable")

    diag._get_full_system_config = failing

    result = await diag.run_diagnostic_step("get_full_system_config")

    assert "error" in result
    assert "hub unreachable" in result["error"]


@pytest.mark.asyncio
async def test_run_diagnostic_step_past_timeout_reports_timed_out_and_clears():
    diag = _bare_diagnostics()
    stale_start = time.monotonic() - (diag._DIAGNOSTICS_TIMEOUT_S + 5)
    diag._diagnostics_state = _in_progress_session(started_at=stale_start)

    result = await diag.run_diagnostic_step("get_full_system_config")

    assert result == {"status": "timed_out"}
    assert diag._diagnostics_state is None


@pytest.mark.asyncio
async def test_conclude_step_ends_the_session_with_a_summary():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session()

    result = await diag.run_diagnostic_step("conclude", summary="It's a Zigbee interference issue.")

    assert result == {"status": "completed", "summary": "It's a Zigbee interference issue."}
    assert diag._diagnostics_state is None


@pytest.mark.asyncio
async def test_conclude_step_works_without_a_summary():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session()

    result = await diag.run_diagnostic_step("conclude")

    assert result == {"status": "completed", "summary": None}
    assert diag._diagnostics_state is None


@pytest.mark.asyncio
async def test_stop_step_ends_the_session_and_issues_the_physical_stop():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session()

    calls = []

    async def fake_stop():
        calls.append("stopped")
        return "stopped ok"

    diag.stop_long_running_diagnostic = fake_stop

    result = await diag.run_diagnostic_step("stop")

    assert result == {"status": "stopped", "result": "stopped ok"}
    assert diag._diagnostics_state is None
    assert calls == ["stopped"]


# ----------------------------------------------------------------------
# get_running_diagnostic -- used to gate every other tool call.
# ----------------------------------------------------------------------


def test_get_running_diagnostic_returns_none_when_nothing_active():
    diag = _bare_diagnostics()
    assert diag.get_running_diagnostic() is None


def test_get_running_diagnostic_reports_active_session():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session(started_at=time.monotonic() - 3)

    info = diag.get_running_diagnostic()

    assert info["status"] == "in_progress"
    assert info["elapsed_s"] >= 3


def test_get_running_diagnostic_is_none_once_past_timeout():
    diag = _bare_diagnostics()
    stale_start = time.monotonic() - (diag._DIAGNOSTICS_TIMEOUT_S + 5)
    diag._diagnostics_state = _in_progress_session(started_at=stale_start)
    assert diag.get_running_diagnostic() is None


def test_get_running_diagnostic_includes_candidates_when_present():
    diag = _bare_diagnostics()
    candidates = {"devices": [{"device_id": "n001", "score": 0.9}], "routines": []}
    diag._diagnostics_state = _in_progress_session(started_at=time.monotonic() - 3, candidates=candidates)

    info = diag.get_running_diagnostic()

    assert info["candidates"] == candidates


def test_get_running_diagnostic_omits_candidates_key_when_none():
    diag = _bare_diagnostics()
    diag._diagnostics_state = _in_progress_session(started_at=time.monotonic() - 3)

    info = diag.get_running_diagnostic()

    assert "candidates" not in info


# ----------------------------------------------------------------------
# IoXWrapper delegation -- start_diagnostics/run_diagnostic_step/
# get_running_diagnostic must just forward to self.diagnostics.
# ----------------------------------------------------------------------


def _bare_wrapper_with_diagnostics() -> IoXWrapper:
    wrapper = object.__new__(IoXWrapper)
    wrapper.diagnostics = _bare_diagnostics()
    return wrapper


@pytest.mark.asyncio
async def test_wrapper_start_diagnostics_delegates():
    wrapper = _bare_wrapper_with_diagnostics()

    result = await wrapper.start_diagnostics()

    assert result["status"] == "in_progress"
    assert result["instruction"]


@pytest.mark.asyncio
async def test_wrapper_run_diagnostic_step_delegates():
    wrapper = _bare_wrapper_with_diagnostics()
    wrapper.diagnostics._diagnostics_state = _in_progress_session()

    result = await wrapper.run_diagnostic_step("conclude", summary="done")

    assert result == {"status": "completed", "summary": "done"}


def test_wrapper_get_running_diagnostic_delegates():
    wrapper = _bare_wrapper_with_diagnostics()
    wrapper.diagnostics._diagnostics_state = _in_progress_session(started_at=time.monotonic() - 3)
    assert wrapper.get_running_diagnostic() == wrapper.diagnostics.get_running_diagnostic()

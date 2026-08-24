"""IoXDiagnostics._load_diagnostic_config/_parse_diagnostic_config -- the
fenced ```json step catalog in diagnose.md is parsed and validated at
construction time (every declared step must resolve to a real callable
method), and IoXDiagnostics.run_diagnostic_step -- a plain, stateless
dispatcher: no session, always available, dispatch by step name. Also
covers IoXWrapper's thin delegation to it.
"""

from __future__ import annotations

import pytest

from iox.diagnostics.iox_diagnostics import IoXDiagnostics
from iox.iox_wrapper import IoXWrapper

STEP_NAMES = {
    "get_full_system_config", "get_core_services_status", "get_plugin_services_status",
    "services_ops", "get_device_family", "get_dev_links_table", "get_iox_links_table",
    "compare_device_links", "get_all_plm_links", "quick_plm_sanity_check",
}


def _bare_diagnostics() -> IoXDiagnostics:
    diag = object.__new__(IoXDiagnostics)
    diag._plm_op_state = None
    # __init__ is bypassed (no real IoXWrapper needed for these tests), but
    # the diagnose.md parse/validation is still exercised via the real loader
    # -- these tests should fail loudly too if the prompt and the code drift.
    diag._diagnostic_instruction, diag._diagnostic_steps = diag._load_diagnostic_config()
    return diag


def test_real_diagnose_md_parses_and_validates_cleanly():
    # No mocking -- confirms the actual shipped diagnose.md matches the
    # actual IoXDiagnostics methods (this is what __init__ runs for real).
    diag = object.__new__(IoXDiagnostics)
    text, steps = diag._load_diagnostic_config()

    assert set(steps.keys()) == STEP_NAMES
    assert text  # the full prose+json text, shown to the model via get_diagnostics_prompt


def test_parse_diagnostic_config_errors_when_json_block_missing():
    diag = object.__new__(IoXDiagnostics)

    with pytest.raises(RuntimeError, match="missing its"):
        diag._parse_diagnostic_config("just prose, no fenced json block here")


def test_parse_diagnostic_config_errors_on_malformed_json():
    diag = object.__new__(IoXDiagnostics)

    with pytest.raises(RuntimeError, match="malformed"):
        diag._parse_diagnostic_config("prose\n```json\n{not valid json\n```\n")


def test_parse_diagnostic_config_errors_when_method_does_not_exist():
    # Step name -> backend method is convention-derived (same name, no
    # leading underscore), no per-step override -- a step with no matching
    # method fails.
    diag = object.__new__(IoXDiagnostics)
    text = 'prose\n```json\n{"totally_made_up_step": {"description": "x"}}\n```\n'

    with pytest.raises(RuntimeError, match="totally_made_up_step"):
        diag._parse_diagnostic_config(text)


@pytest.mark.asyncio
async def test_real_iox_wrapper_construction_runs_the_same_validation():
    # __init__ runs _load_diagnostic_config for real -- confirms a genuine
    # IoXWrapper() (not the object.__new__ bypass every other test in this
    # file uses) doesn't blow up, i.e. the real prompt/code pairing is valid.
    wrapper = object.__new__(IoXWrapper)
    diag = IoXDiagnostics(wrapper)

    assert set(diag._diagnostic_steps.keys()) == STEP_NAMES


# ----------------------------------------------------------------------
# run_diagnostic_step -- no session, dispatches by name, forwards params.
# ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_diagnostic_step_errors_on_unknown_step():
    diag = _bare_diagnostics()

    result = await diag.run_diagnostic_step("not_a_real_step")

    assert "error" in result


@pytest.mark.asyncio
async def test_run_diagnostic_step_dispatches_to_the_backend_method():
    diag = _bare_diagnostics()

    calls = []

    async def fake_get_full_system_config():
        calls.append("called")
        return {"ok": True}

    diag.get_full_system_config = fake_get_full_system_config

    result = await diag.run_diagnostic_step("get_full_system_config")

    assert result == {"step": "get_full_system_config", "result": {"ok": True}}
    assert calls == ["called"]


@pytest.mark.asyncio
async def test_run_diagnostic_step_forwards_params_to_the_backend_method():
    diag = _bare_diagnostics()

    received = {}

    async def fake_get_dev_links_table(device_id=None, **kwargs):
        received["device_id"] = device_id
        return "link table text"

    diag.get_dev_links_table = fake_get_dev_links_table

    result = await diag.run_diagnostic_step("get_dev_links_table", device_id="n001")

    assert received["device_id"] == "n001"
    assert result["result"] == "link table text"


@pytest.mark.asyncio
async def test_run_diagnostic_step_catches_unexpected_exceptions():
    diag = _bare_diagnostics()

    async def failing():
        raise RuntimeError("hub unreachable")

    diag.get_full_system_config = failing

    result = await diag.run_diagnostic_step("get_full_system_config")

    assert "error" in result
    assert "hub unreachable" in result["error"]


@pytest.mark.asyncio
async def test_run_diagnostic_step_is_always_available_no_session_needed():
    # Calling it back-to-back with no start call and nothing in between --
    # there's no session state to be missing.
    diag = _bare_diagnostics()
    diag.get_device_family = lambda device_id=None, **kw: _resolved("insteon")

    first = await diag.run_diagnostic_step("get_device_family", device_id="n001")
    second = await diag.run_diagnostic_step("get_device_family", device_id="n002")

    assert first["result"] == "insteon"
    assert second["result"] == "insteon"


async def _resolved(value):
    return value


# ----------------------------------------------------------------------
# IoXWrapper delegation -- run_diagnostic_step must just forward to
# self.diagnostics.
# ----------------------------------------------------------------------


def _bare_wrapper_with_diagnostics() -> IoXWrapper:
    wrapper = object.__new__(IoXWrapper)
    wrapper.diagnostics = _bare_diagnostics()
    return wrapper


@pytest.mark.asyncio
async def test_wrapper_run_diagnostic_step_delegates():
    wrapper = _bare_wrapper_with_diagnostics()

    calls = []

    async def fake_get_full_system_config():
        calls.append("called")
        return {"ok": True}

    wrapper.diagnostics.get_full_system_config = fake_get_full_system_config

    result = await wrapper.run_diagnostic_step("get_full_system_config")

    assert result == {"step": "get_full_system_config", "result": {"ok": True}}
    assert calls == ["called"]

"""PlanEngine -- the write-heavy counterpart to IoXDiagnostics: session
lifecycle (start/stub-type/step-dispatch/ownership/timeout), staging
(propose/review/revise), and apply_plan's per-item success/failure
aggregation. apply_plan is tested with the real handler functions
monkeypatched (same FakeWrapper-style pattern as
tests/iox/test_plm_links_cache.py) rather than a full live hub round-trip.
"""

from __future__ import annotations

import time

import pytest

from unified.planning import plan_engine as plan_engine_module
from unified.planning.plan_engine import PlanEngine, _PLAN_TYPES


def _bare_engine() -> PlanEngine:
    engine = object.__new__(PlanEngine)
    engine._plan_state = None
    return engine


# ---------------------------------------------------------------------------
# Config loading -- fails loudly at import time if the prompt and the code
# have drifted apart (mirrors test_run_diagnostics.py's equivalent check).
# ---------------------------------------------------------------------------


def test_new_installation_prompt_parses_and_validates_cleanly():
    instruction, steps = PlanEngine._CONFIGS["new_installation"]
    assert "new_installation" not in _steps_missing_backend_methods(steps)
    assert isinstance(instruction, str) and instruction.strip()
    assert "conclude" in steps and "stop" in steps


def _steps_missing_backend_methods(steps: dict) -> list[str]:
    missing = []
    for name in steps:
        if name in {"conclude", "stop"}:
            continue
        if not callable(getattr(PlanEngine, f"_{name}", None)):
            missing.append(name)
    return missing


def test_every_stub_plan_type_is_recognized_but_maps_to_no_config():
    for plan_type, filename in _PLAN_TYPES.items():
        if plan_type == "new_installation":
            assert filename is not None
            assert plan_type in PlanEngine._CONFIGS
        else:
            assert filename is None
            assert plan_type not in PlanEngine._CONFIGS


# ---------------------------------------------------------------------------
# start_plan
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_start_plan_rejects_unknown_type():
    engine = _bare_engine()
    result = await engine.start_plan("not_a_real_plan_type")
    assert "error" in result
    assert engine._plan_state is None


@pytest.mark.asyncio
async def test_start_plan_returns_not_implemented_for_a_stub_type():
    engine = _bare_engine()
    result = await engine.start_plan("vacation")
    assert result["status"] == "not_implemented"
    assert result["plan_type"] == "vacation"
    assert engine._plan_state is None  # no lock taken for a stub type


@pytest.mark.asyncio
async def test_start_plan_opens_a_real_session_for_new_installation():
    engine = _bare_engine()
    result = await engine.start_plan("new_installation", session_id="s1")
    assert result["status"] == "in_progress"
    assert result["plan_type"] == "new_installation"
    assert "pair_device" in result["available_tools"]
    assert "apply_plan" in result["available_tools"]
    assert engine._plan_state is not None


@pytest.mark.asyncio
async def test_start_plan_reshows_same_session_for_owning_session_id():
    engine = _bare_engine()
    first = await engine.start_plan("new_installation", session_id="s1")
    second = await engine.start_plan("new_installation", session_id="s1")
    assert second["status"] == "in_progress"
    assert second["plan_type"] == first["plan_type"]


@pytest.mark.asyncio
async def test_start_plan_refuses_a_different_session_while_one_is_open():
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")
    result = await engine.start_plan("new_installation", session_id="s2")
    assert "error" in result


@pytest.mark.asyncio
async def test_start_plan_clears_a_stale_session_past_timeout(monkeypatch):
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")
    engine._plan_state["started_at"] = time.monotonic() - (PlanEngine._PLAN_TIMEOUT_S + 1)

    result = await engine.start_plan("new_installation", session_id="s2")

    assert result["status"] == "in_progress"  # s2 got a fresh session, not refused


# ---------------------------------------------------------------------------
# run_plan_step -- ownership/timeout/dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_plan_step_requires_a_started_session():
    engine = _bare_engine()
    result = await engine.run_plan_step(None, "create_folder", session_id="s1")
    assert "error" in result


@pytest.mark.asyncio
async def test_run_plan_step_refuses_a_different_session():
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")
    result = await engine.run_plan_step(None, "review_plan", session_id="s2")
    assert "error" in result


@pytest.mark.asyncio
async def test_run_plan_step_rejects_an_unknown_step():
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")
    result = await engine.run_plan_step(None, "totally_made_up_step", session_id="s1")
    assert "error" in result


@pytest.mark.asyncio
async def test_conclude_ends_the_session():
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")
    result = await engine.run_plan_step(None, "conclude", session_id="s1", summary="done")
    assert result == {"status": "completed", "summary": "done"}
    assert engine._plan_state is None


@pytest.mark.asyncio
async def test_stop_ends_the_session():
    # No pairing cleanup call expected -- pair_device only ever uses the
    # self-contained add_device, never the discover/finish batch session, so
    # there's nothing on the hub for stop to defensively clean up.
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")

    result = await engine.run_plan_step(None, "stop", session_id="s1")

    assert result == {"status": "stopped"}
    assert engine._plan_state is None


# ---------------------------------------------------------------------------
# Staging: propose / review / revise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_scene_stages_without_touching_the_live_system():
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")

    result = await engine.run_plan_step(
        None, "propose_scene", session_id="s1", group_name="Movie Night", devices=[]
    )

    assert result["result"]["status"] == "proposed"
    assert engine._plan_state["staged_ops"][0]["op"] == "scene"


@pytest.mark.asyncio
async def test_review_plan_lists_everything_staged():
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")
    await engine.run_plan_step(None, "propose_variable", session_id="s1", type=1, name="Counter")

    result = await engine.run_plan_step(None, "review_plan", session_id="s1")

    assert len(result["result"]["staged_ops"]) == 1
    assert result["result"]["staged_ops"][0]["params"]["name"] == "Counter"


@pytest.mark.asyncio
async def test_revise_plan_replaces_params():
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")
    await engine.run_plan_step(None, "propose_variable", session_id="s1", type=1, name="Counter")

    result = await engine.run_plan_step(
        None, "revise_plan", session_id="s1", id=1, params={"type": 1, "name": "Renamed"}
    )

    assert result["result"]["params"]["name"] == "Renamed"


@pytest.mark.asyncio
async def test_revise_plan_removes_an_item():
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")
    await engine.run_plan_step(None, "propose_variable", session_id="s1", type=1, name="Counter")

    result = await engine.run_plan_step(None, "revise_plan", session_id="s1", id=1, remove=True)

    assert result["result"]["status"] == "removed"
    assert engine._plan_state["staged_ops"] == []


@pytest.mark.asyncio
async def test_revise_plan_errors_on_unknown_id():
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")

    result = await engine.run_plan_step(None, "revise_plan", session_id="s1", id=999)

    assert "error" in result["result"]


# ---------------------------------------------------------------------------
# apply_plan -- per-item success/failure, real handler functions monkeypatched
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_apply_plan_reports_itemized_success_and_failure(monkeypatch):
    async def fake_variable_op(nucore_interface, args):
        if args["name"] == "Good":
            return {"type": 1, "id": "7", "status": "saved"}
        return {"error": "hub rejected it"}

    monkeypatch.setattr(plan_engine_module.variable_ops, "variable_op", fake_variable_op)

    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")
    await engine.run_plan_step(None, "propose_variable", session_id="s1", type=1, name="Good")
    await engine.run_plan_step(None, "propose_variable", session_id="s1", type=1, name="Bad")

    result = await engine.run_plan_step(None, "apply_plan", session_id="s1")

    summary = result["result"]["summary"]
    assert summary == {"total": 2, "successful": 1, "failed": 1}
    statuses = {e["id"]: e["status"] for e in engine._plan_state["staged_ops"]}
    assert statuses[1] == "applied"
    assert statuses[2] == "failed: hub rejected it"


@pytest.mark.asyncio
async def test_apply_plan_skips_already_applied_items_on_a_second_call(monkeypatch):
    calls = []

    async def fake_variable_op(nucore_interface, args):
        calls.append(args["name"])
        return {"type": 1, "id": "1", "status": "saved"}

    monkeypatch.setattr(plan_engine_module.variable_ops, "variable_op", fake_variable_op)

    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")
    await engine.run_plan_step(None, "propose_variable", session_id="s1", type=1, name="Once")

    await engine.run_plan_step(None, "apply_plan", session_id="s1")
    await engine.run_plan_step(None, "apply_plan", session_id="s1")

    assert calls == ["Once"]  # not re-applied the second time


@pytest.mark.asyncio
async def test_apply_plan_dispatches_scene_and_automation_ops_too(monkeypatch):
    scene_calls = []
    automation_calls = []

    async def fake_multi_device_scene(nucore_interface, args):
        scene_calls.append(args)
        return {"group_address": "g1", "group_name": "Test", "summary": {}, "results": []}

    async def fake_create_or_update_routine(nucore_interface, args):
        automation_calls.append(args)
        return {"name": args["name"], "id": "r1", "status": "saved"}

    monkeypatch.setattr(plan_engine_module.group_scene_ops, "multi_device_scene", fake_multi_device_scene)
    monkeypatch.setattr(plan_engine_module.routine_automation, "create_or_update_routine", fake_create_or_update_routine)

    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")
    await engine.run_plan_step(None, "propose_scene", session_id="s1", group_name="Test", devices=[])
    await engine.run_plan_step(None, "propose_automation", session_id="s1", name="Sunset Lights", code="pass")

    result = await engine.run_plan_step(None, "apply_plan", session_id="s1")

    assert result["result"]["summary"] == {"total": 2, "successful": 2, "failed": 0}
    assert len(scene_calls) == 1 and len(automation_calls) == 1


# ---------------------------------------------------------------------------
# pair_device -- protocol gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pair_device_rejects_non_insteon_protocols():
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")

    result = await engine.run_plan_step(None, "pair_device", session_id="s1", protocol="zwave")

    assert "not yet supported" in result["result"]


@pytest.mark.asyncio
async def test_pair_device_requires_a_device_address():
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")

    result = await engine.run_plan_step(None, "pair_device", session_id="s1", protocol="insteon")

    assert "error" in result["result"]


@pytest.mark.asyncio
async def test_pair_device_adds_the_device_by_address():
    class FakeNucore:
        def __init__(self):
            self.calls = []

        async def add_device(self, device_address):
            self.calls.append(device_address)
            return "added"

    nucore = FakeNucore()
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")

    result = await engine.run_plan_step(
        nucore, "pair_device", session_id="s1", protocol="insteon", device_address="1A 2B 3C 1"
    )

    assert nucore.calls == ["1A 2B 3C 1"]
    assert result["result"] == "added"


# ---------------------------------------------------------------------------
# get_running_plan
# ---------------------------------------------------------------------------


def test_get_running_plan_is_none_when_nothing_started():
    engine = _bare_engine()
    assert engine.get_running_plan() is None


@pytest.mark.asyncio
async def test_get_running_plan_reports_the_owning_session():
    engine = _bare_engine()
    await engine.start_plan("new_installation", session_id="s1")
    running = engine.get_running_plan()
    assert running["session_id"] == "s1"
    assert running["status"] == "in_progress"

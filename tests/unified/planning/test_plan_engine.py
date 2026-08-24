"""PlanEngine -- the write-heavy counterpart to IoXDiagnostics: no session
wrapper any more, just session_id-scoped staged-ops buckets (propose/review/
revise/discard/apply) plus the one truly stateless method (get_plan_prompt).
Covers what's awkward to exercise through execute_tool: prompt-loading
validation and the idle-staleness backstop (both need direct access to
PlanEngine's internals / the clock). Dispatch-layer behavior (routing,
session isolation via execute_tool, the shared PLM lock) is covered in
tests/unified/handlers/test_plan.py.
"""

from __future__ import annotations

import time

import pytest

from unified.planning import plan_engine as plan_engine_module
from unified.planning.plan_engine import PlanEngine, _PLAN_TYPES


# ---------------------------------------------------------------------------
# Prompt loading -- fails loudly at import time if a prompt file is missing,
# mirrors the old config-loading check.
# ---------------------------------------------------------------------------


def test_new_installation_prompt_loaded_and_non_empty():
    assert "new_installation" in PlanEngine._PROMPTS
    assert isinstance(PlanEngine._PROMPTS["new_installation"], str)
    assert PlanEngine._PROMPTS["new_installation"].strip()


def test_every_stub_plan_type_is_recognized_but_has_no_prompt():
    for plan_type, filename in _PLAN_TYPES.items():
        if plan_type == "new_installation":
            assert filename is not None
            assert plan_type in PlanEngine._PROMPTS
        else:
            assert filename is None
            assert plan_type not in PlanEngine._PROMPTS


@pytest.mark.asyncio
async def test_get_plan_prompt_rejects_unknown_type():
    engine = PlanEngine()
    result = await engine.get_plan_prompt("not_a_real_plan_type")
    assert "error" in result


@pytest.mark.asyncio
async def test_get_plan_prompt_returns_not_implemented_for_a_stub_type():
    engine = PlanEngine()
    result = await engine.get_plan_prompt("vacation")
    assert result["status"] == "not_implemented"
    assert result["plan_type"] == "vacation"


@pytest.mark.asyncio
async def test_get_plan_prompt_returns_real_text_for_new_installation():
    engine = PlanEngine()
    result = await engine.get_plan_prompt("new_installation")
    assert result["plan_type"] == "new_installation"
    assert result["prompt"] == PlanEngine._PROMPTS["new_installation"]


# ---------------------------------------------------------------------------
# Session-scoped staged-ops bucket -- creation, isolation, idle staleness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_session_key_is_used_when_no_session_id_given():
    engine = PlanEngine()
    await engine.propose_variable(session_id=None, type=1, name="No Session")
    result = await engine.review_plan(session_id=None)
    assert len(result["staged_ops"]) == 1
    assert "default" in engine._plan_sessions


@pytest.mark.asyncio
async def test_stale_session_is_cleared_after_the_idle_timeout(monkeypatch):
    engine = PlanEngine()
    await engine.propose_variable(session_id="s1", type=1, name="Old")

    fake_now = time.monotonic() + PlanEngine._PLAN_TIMEOUT_S + 1
    monkeypatch.setattr(plan_engine_module.time, "monotonic", lambda: fake_now)

    result = await engine.review_plan(session_id="s1")

    assert result["staged_ops"] == []  # stale bucket cleared, fresh one created silently


@pytest.mark.asyncio
async def test_touching_a_session_resets_its_idle_clock(monkeypatch):
    real_monotonic = time.monotonic
    clock = {"t": real_monotonic()}
    monkeypatch.setattr(plan_engine_module.time, "monotonic", lambda: clock["t"])

    engine = PlanEngine()
    await engine.propose_variable(session_id="s1", type=1, name="A")

    # Advance close to, but not past, the timeout, then touch the session --
    # if the clock weren't reset on touch, a second identical advance would
    # tip it over into staleness. It doesn't, because review_plan already
    # touched it once.
    clock["t"] += PlanEngine._PLAN_TIMEOUT_S - 1
    await engine.review_plan(session_id="s1")
    clock["t"] += PlanEngine._PLAN_TIMEOUT_S - 1

    result = await engine.review_plan(session_id="s1")

    assert len(result["staged_ops"]) == 1  # still there -- never went stale


# ---------------------------------------------------------------------------
# Staging: propose / review / revise / discard
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_scene_stages_without_touching_the_live_system():
    engine = PlanEngine()
    result = await engine.propose_scene(session_id="s1", group_name="Movie Night", devices=[])
    assert result["status"] == "proposed"
    assert engine._plan_sessions["s1"]["staged_ops"][0]["op"] == "scene"


@pytest.mark.asyncio
async def test_revise_plan_replaces_params():
    engine = PlanEngine()
    await engine.propose_variable(session_id="s1", type=1, name="Counter")
    result = await engine.revise_plan(session_id="s1", id=1, params={"type": 1, "name": "Renamed"})
    assert result["params"]["name"] == "Renamed"


@pytest.mark.asyncio
async def test_revise_plan_removes_an_item():
    engine = PlanEngine()
    await engine.propose_variable(session_id="s1", type=1, name="Counter")
    result = await engine.revise_plan(session_id="s1", id=1, remove=True)
    assert result["status"] == "removed"
    assert engine._plan_sessions["s1"]["staged_ops"] == []


@pytest.mark.asyncio
async def test_discard_plan_removes_the_whole_bucket():
    engine = PlanEngine()
    await engine.propose_variable(session_id="s1", type=1, name="Counter")
    result = await engine.discard_plan(session_id="s1")
    assert result == {"status": "discarded"}
    assert "s1" not in engine._plan_sessions


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

    engine = PlanEngine()
    await engine.propose_variable(session_id="s1", type=1, name="Good")
    await engine.propose_variable(session_id="s1", type=1, name="Bad")

    result = await engine.apply_plan(None, session_id="s1")

    summary = result["summary"]
    assert summary == {"total": 2, "successful": 1, "failed": 1}
    statuses = {e["id"]: e["status"] for e in engine._plan_sessions["s1"]["staged_ops"]}
    assert statuses[1] == "applied"
    assert statuses[2] == "failed: hub rejected it"


@pytest.mark.asyncio
async def test_apply_plan_skips_already_applied_items_on_a_second_call(monkeypatch):
    calls = []

    async def fake_variable_op(nucore_interface, args):
        calls.append(args["name"])
        return {"type": 1, "id": "1", "status": "saved"}

    monkeypatch.setattr(plan_engine_module.variable_ops, "variable_op", fake_variable_op)

    engine = PlanEngine()
    await engine.propose_variable(session_id="s1", type=1, name="Once")

    await engine.apply_plan(None, session_id="s1")
    await engine.apply_plan(None, session_id="s1")

    assert calls == ["Once"]


# ---------------------------------------------------------------------------
# pair_device -- protocol gating and the shared PLM lock
# ---------------------------------------------------------------------------


class _FakeNucore:
    def __init__(self, busy_error=None):
        self.pairing_calls = []
        self.plm_calls = []
        self.busy_error = busy_error

    async def begin_plm_op(self, step):
        self.plm_calls.append(f"begin:{step}")
        return self.busy_error

    async def end_plm_op(self):
        self.plm_calls.append("end")

    async def add_device(self, device_address):
        self.pairing_calls.append(device_address)
        return "added"


@pytest.mark.asyncio
async def test_pair_device_rejects_non_insteon_protocols():
    engine = PlanEngine()
    result = await engine.pair_device(_FakeNucore(), protocol="zwave")
    assert "not yet supported" in result


@pytest.mark.asyncio
async def test_pair_device_requires_a_device_address():
    engine = PlanEngine()
    result = await engine.pair_device(_FakeNucore(), protocol="insteon")
    assert "error" in result


@pytest.mark.asyncio
async def test_pair_device_adds_the_device_by_address_through_the_shared_lock():
    nucore = _FakeNucore()
    engine = PlanEngine()

    result = await engine.pair_device(nucore, protocol="insteon", device_address="1A 2B 3C 1")

    assert nucore.pairing_calls == ["1A 2B 3C 1"]
    assert result == "added"
    assert nucore.plm_calls == ["begin:pair_device", "end"]


@pytest.mark.asyncio
async def test_pair_device_refused_when_the_shared_lock_is_busy():
    nucore = _FakeNucore(busy_error={"error": "a PLM operation is already in progress"})
    engine = PlanEngine()

    result = await engine.pair_device(nucore, protocol="insteon", device_address="1A 2B 3C 1")

    assert result == nucore.busy_error
    assert nucore.pairing_calls == []

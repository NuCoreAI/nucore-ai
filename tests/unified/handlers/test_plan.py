"""End-to-end: the ten standalone Plan tools dispatched through execute_tool
-- confirms TOOL_HANDLERS routing, the lazy per-instance PlanEngine
attachment, session-scoped staged-ops isolation between conversations, the
shared PLM lock between pair_device and diagnostics, and that the old
session-wrapper tools (start_plan/run_plan_step) and its blanket lock are
gone. No blanket lock any more -- these are ordinary, always-available
tools, same as get_property/node_op.
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import TOOL_HANDLERS, execute_tool
from unified.handlers import plan
from unified.planning import plan_engine as plan_engine_module


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.pairing_calls: list[str] = []
        self.plm_calls: list[str] = []
        self.plm_busy_error: dict | None = None

    async def diagnostics_get_full_system_config(self, **kwargs): raise NotImplementedError
    async def diagnostics_get_device_family(self, device_id, **kwargs): raise NotImplementedError
    async def diagnostics_get_dev_links_table(self, device_id, **kwargs): raise NotImplementedError
    async def diagnostics_get_iox_links_table(self, device_id, **kwargs): raise NotImplementedError
    async def diagnostics_compare_device_links(self, device_id, **kwargs): raise NotImplementedError
    async def diagnostics_get_all_plm_links(self, refresh_plm_links=False, **kwargs): raise NotImplementedError
    async def diagnostics_quick_plm_sanity_check(self, **kwargs): raise NotImplementedError

    async def begin_plm_op(self, step):
        self.plm_calls.append(f"begin:{step}")
        return self.plm_busy_error

    async def end_plm_op(self):
        self.plm_calls.append("end")

    async def add_device(self, device_address, **kwargs):
        self.pairing_calls.append(device_address)
        return {"status": "added"}

    async def discover_devices(self): raise NotImplementedError
    async def finish_device_discovery(self): raise NotImplementedError

    async def _load(self, **kwargs): raise NotImplementedError
    async def _load_routines(self): raise NotImplementedError
    async def _load_variables(self): pass
    async def send_commands(self, commands): raise NotImplementedError
    async def create_automation_routine(self, trigger): raise NotImplementedError
    async def update_routine(self, program): raise NotImplementedError
    async def get_routine(self, routine_id): raise NotImplementedError
    async def get_properties(self, device_id): raise NotImplementedError
    def get_device_name(self, device_id): raise NotImplementedError
    def get_device_id(self, device_str): raise NotImplementedError
    async def get_all_routines_summary(self): raise NotImplementedError
    async def get_routine_summary(self, routine_id): raise NotImplementedError
    async def get_all_routines(self): raise NotImplementedError
    async def add_node(self, node_name, type): raise NotImplementedError
    async def node_ops(self, node_id, operation, **kwargs): raise NotImplementedError
    async def routine_ops(self, routine_id, operation): raise NotImplementedError
    async def variable_ops(self, var_type, var_id, operation, **kwargs): raise NotImplementedError
    def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    def group_scene_update_link(self, *a, **kw): raise NotImplementedError
    def group_scene_get_node_roles(self, *a, **kw): raise NotImplementedError
    def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError


@pytest.mark.asyncio
async def test_get_engine_attaches_and_reuses_the_same_instance_per_backend():
    backend = FakeBackend()
    engine1 = plan._get_engine(backend)
    engine2 = plan._get_engine(backend)
    assert engine1 is engine2
    assert backend._plan_engine is engine1


# ---------------------------------------------------------------------------
# get_plan_prompt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_plan_prompt_defaults_to_new_installation():
    backend = FakeBackend()
    result = await execute_tool("get_plan_prompt", {}, nucore_interface=backend)
    assert result["plan_type"] == "new_installation"
    assert isinstance(result["prompt"], str) and result["prompt"].strip()


@pytest.mark.asyncio
async def test_get_plan_prompt_returns_not_implemented_for_a_stub_type():
    backend = FakeBackend()
    result = await execute_tool("get_plan_prompt", {"plan_type": "holidays"}, nucore_interface=backend)
    assert result["status"] == "not_implemented"


@pytest.mark.asyncio
async def test_get_plan_prompt_rejects_an_unknown_type():
    backend = FakeBackend()
    result = await execute_tool("get_plan_prompt", {"plan_type": "not_a_real_type"}, nucore_interface=backend)
    assert "error" in result


# ---------------------------------------------------------------------------
# create_folder
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_folder_requires_new_name():
    backend = FakeBackend()
    result = await execute_tool("create_folder", {}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_create_folder_dispatches_to_node_op(monkeypatch):
    calls = []

    async def fake_node_op(nucore_interface, args):
        calls.append(args)
        return {"id": "f1", "name": args["new_name"], "status": "created"}

    monkeypatch.setattr(plan_engine_module.node_ops, "node_op", fake_node_op)

    backend = FakeBackend()
    result = await execute_tool("create_folder", {"new_name": "Garage"}, nucore_interface=backend)

    assert result == {"id": "f1", "name": "Garage", "status": "created"}
    assert calls == [{"operation": "add_folder", "new_name": "Garage"}]


# ---------------------------------------------------------------------------
# pair_device -- protocol/param gating and the shared PLM lock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pair_device_rejects_non_insteon_protocols():
    backend = FakeBackend()
    result = await execute_tool("pair_device", {"protocol": "zwave"}, nucore_interface=backend)
    assert "not yet supported" in result
    assert backend.plm_calls == []  # never touched the lock for a rejected protocol


@pytest.mark.asyncio
async def test_pair_device_requires_a_device_address():
    backend = FakeBackend()
    result = await execute_tool("pair_device", {"protocol": "insteon"}, nucore_interface=backend)
    assert "error" in result
    assert backend.plm_calls == []


@pytest.mark.asyncio
async def test_pair_device_claims_and_releases_the_shared_plm_lock():
    backend = FakeBackend()
    result = await execute_tool(
        "pair_device", {"protocol": "insteon", "device_address": "1A 2B 3C 1"}, nucore_interface=backend
    )
    assert backend.pairing_calls == ["1A 2B 3C 1"]
    assert result == {"status": "added"}
    assert backend.plm_calls == ["begin:pair_device", "end"]


@pytest.mark.asyncio
async def test_pair_device_refused_immediately_when_plm_is_busy():
    # Same shared lock the 4 diagnostics tools use -- this proves pair_device
    # actually joins it rather than a second, independent mutex: a "busy"
    # response from begin_plm_op (as if a diagnostics tool were mid-flight)
    # refuses pair_device immediately, without ever touching add_device.
    backend = FakeBackend()
    backend.plm_busy_error = {"error": "a PLM operation ('get_all_plm_links') is already in progress"}

    result = await execute_tool(
        "pair_device", {"protocol": "insteon", "device_address": "1A 2B 3C 1"}, nucore_interface=backend
    )

    assert result == backend.plm_busy_error
    assert backend.pairing_calls == []
    assert backend.plm_calls == ["begin:pair_device"]  # never reached end -- add_device was never called


# ---------------------------------------------------------------------------
# Staging: propose / review / revise / discard, session-scoped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_propose_scene_recovers_stringified_json_params():
    backend = FakeBackend()
    result = await execute_tool(
        "propose_scene",
        {"params": '{"group_name": "Movie Night", "devices": []}'},
        nucore_interface=backend,
        session_id="s1",
    )
    assert result["op"] == "scene"
    assert result["status"] == "proposed"


@pytest.mark.asyncio
async def test_propose_scene_rejects_a_non_json_string_params():
    backend = FakeBackend()
    result = await execute_tool(
        "propose_scene", {"params": "not json"}, nucore_interface=backend, session_id="s1"
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_propose_then_review_shows_the_staged_item():
    backend = FakeBackend()
    await execute_tool(
        "propose_variable", {"params": {"type": 1, "name": "Counter"}}, nucore_interface=backend, session_id="s1"
    )

    result = await execute_tool("review_plan", {}, nucore_interface=backend, session_id="s1")

    assert len(result["staged_ops"]) == 1
    assert result["staged_ops"][0]["params"]["name"] == "Counter"


@pytest.mark.asyncio
async def test_two_sessions_have_isolated_staged_ops():
    backend = FakeBackend()
    await execute_tool(
        "propose_variable", {"params": {"type": 1, "name": "S1 Var"}}, nucore_interface=backend, session_id="s1"
    )
    await execute_tool(
        "propose_variable", {"params": {"type": 1, "name": "S2 Var"}}, nucore_interface=backend, session_id="s2"
    )

    s1 = await execute_tool("review_plan", {}, nucore_interface=backend, session_id="s1")
    s2 = await execute_tool("review_plan", {}, nucore_interface=backend, session_id="s2")

    assert [op["params"]["name"] for op in s1["staged_ops"]] == ["S1 Var"]
    assert [op["params"]["name"] for op in s2["staged_ops"]] == ["S2 Var"]


@pytest.mark.asyncio
async def test_revise_plan_replaces_params():
    backend = FakeBackend()
    await execute_tool(
        "propose_variable", {"params": {"type": 1, "name": "Counter"}}, nucore_interface=backend, session_id="s1"
    )

    result = await execute_tool(
        "revise_plan", {"id": 1, "params": {"type": 1, "name": "Renamed"}}, nucore_interface=backend, session_id="s1"
    )

    assert result["params"]["name"] == "Renamed"


@pytest.mark.asyncio
async def test_revise_plan_removes_an_item():
    backend = FakeBackend()
    await execute_tool(
        "propose_variable", {"params": {"type": 1, "name": "Counter"}}, nucore_interface=backend, session_id="s1"
    )

    result = await execute_tool("revise_plan", {"id": 1, "remove": True}, nucore_interface=backend, session_id="s1")
    review = await execute_tool("review_plan", {}, nucore_interface=backend, session_id="s1")

    assert result["status"] == "removed"
    assert review["staged_ops"] == []


@pytest.mark.asyncio
async def test_revise_plan_errors_on_unknown_id():
    backend = FakeBackend()
    result = await execute_tool("revise_plan", {"id": 999}, nucore_interface=backend, session_id="s1")
    assert "error" in result


@pytest.mark.asyncio
async def test_discard_plan_clears_only_its_own_session():
    backend = FakeBackend()
    await execute_tool(
        "propose_variable", {"params": {"type": 1, "name": "S1"}}, nucore_interface=backend, session_id="s1"
    )
    await execute_tool(
        "propose_variable", {"params": {"type": 1, "name": "S2"}}, nucore_interface=backend, session_id="s2"
    )

    discard_result = await execute_tool("discard_plan", {}, nucore_interface=backend, session_id="s1")
    s1_after = await execute_tool("review_plan", {}, nucore_interface=backend, session_id="s1")
    s2_after = await execute_tool("review_plan", {}, nucore_interface=backend, session_id="s2")

    assert discard_result == {"status": "discarded"}
    assert s1_after["staged_ops"] == []
    assert len(s2_after["staged_ops"]) == 1


@pytest.mark.asyncio
async def test_discard_plan_reports_nothing_to_discard_for_an_empty_session():
    backend = FakeBackend()
    result = await execute_tool("discard_plan", {}, nucore_interface=backend, session_id="s1")
    assert result == {"status": "nothing_to_discard"}


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

    backend = FakeBackend()
    await execute_tool(
        "propose_variable", {"params": {"type": 1, "name": "Good"}}, nucore_interface=backend, session_id="s1"
    )
    await execute_tool(
        "propose_variable", {"params": {"type": 1, "name": "Bad"}}, nucore_interface=backend, session_id="s1"
    )

    result = await execute_tool("apply_plan", {}, nucore_interface=backend, session_id="s1")

    assert result["summary"] == {"total": 2, "successful": 1, "failed": 1}


@pytest.mark.asyncio
async def test_apply_plan_skips_already_applied_items_on_a_second_call(monkeypatch):
    calls = []

    async def fake_variable_op(nucore_interface, args):
        calls.append(args["name"])
        return {"type": 1, "id": "1", "status": "saved"}

    monkeypatch.setattr(plan_engine_module.variable_ops, "variable_op", fake_variable_op)

    backend = FakeBackend()
    await execute_tool(
        "propose_variable", {"params": {"type": 1, "name": "Once"}}, nucore_interface=backend, session_id="s1"
    )

    await execute_tool("apply_plan", {}, nucore_interface=backend, session_id="s1")
    await execute_tool("apply_plan", {}, nucore_interface=backend, session_id="s1")

    assert calls == ["Once"]


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

    backend = FakeBackend()
    await execute_tool(
        "propose_scene", {"params": {"group_name": "Test", "devices": []}}, nucore_interface=backend, session_id="s1"
    )
    await execute_tool(
        "propose_automation",
        {"params": {"name": "Sunset Lights", "code": "pass"}},
        nucore_interface=backend,
        session_id="s1",
    )

    result = await execute_tool("apply_plan", {}, nucore_interface=backend, session_id="s1")

    assert result["summary"] == {"total": 2, "successful": 2, "failed": 0}
    assert len(scene_calls) == 1 and len(automation_calls) == 1


# ---------------------------------------------------------------------------
# No blanket lock, no session-wrapper tools
# ---------------------------------------------------------------------------


def test_old_session_tools_are_gone():
    assert "start_plan" not in TOOL_HANDLERS
    assert "run_plan_step" not in TOOL_HANDLERS


@pytest.mark.asyncio
async def test_calling_a_removed_tool_returns_unknown_tool_error():
    backend = FakeBackend()
    result = await execute_tool("start_plan", {}, nucore_interface=backend)
    assert result == {"error": "unknown tool 'start_plan'"}


@pytest.mark.asyncio
async def test_plan_tools_are_not_blocked_by_anything_plan_related():
    # There is no plan blanket lock any more -- staging something in one
    # session doesn't refuse an unrelated tool call in another, or even in
    # the same session.
    backend = FakeBackend()
    await execute_tool(
        "propose_variable", {"params": {"type": 1, "name": "X"}}, nucore_interface=backend, session_id="s1"
    )

    result = await execute_tool("get_plan_prompt", {}, nucore_interface=backend, session_id="s2")

    assert "plan session" not in str(result)

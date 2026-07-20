"""End-to-end: create_or_update_routine dispatched through execute_tool,
compiling via the new unified.routine_compiler package and calling into a
fake NuCoreInterface backend -- confirms the whole chain (DSL -> Trigger
dict -> backend call -> id resolution) works together, not just the
compiler in isolation.
"""

from __future__ import annotations

import pytest

from nucore.nucore_interface import NuCoreInterface
from unified.dispatch import execute_tool


class FakeResp:
    def __init__(self, status_code, body=None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("no body")
        return self._body


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        self.nodes = {}
        self.groups = {}
        self.folders = {}
        self.create_status = 200
        self.update_status = 200
        self.create_error_body = None
        self.update_error_body = None
        self.created_trigger = None
        self.updated_trigger = None
        self.refresh_calls = 0
        self.routine_detail = None

    async def create_automation_routine(self, trigger):
        self.created_trigger = trigger
        return FakeResp(self.create_status, self.create_error_body)

    async def update_routine(self, program):
        self.updated_trigger = program
        return FakeResp(self.update_status, self.update_error_body)

    async def get_routine(self, routine_id):
        if self.routine_detail is None:
            return FakeResp(404)
        return self.routine_detail

    async def _refresh_routines_database(self):
        self.refresh_calls += 1
        self.condensed_routines.append({"id": 99, "name": "Evening Routine", "comment": ""})
        self.routines_changed = False

    async def _load(self, **kwargs): raise NotImplementedError
    async def _load_routines(self): raise NotImplementedError
    async def send_commands(self, commands): raise NotImplementedError
    async def get_properties(self, device_id): raise NotImplementedError
    def get_device_name(self, device_id): raise NotImplementedError
    def get_device_id(self, device_str): raise NotImplementedError
    async def get_all_routines_summary(self): raise NotImplementedError
    async def _load_variables(self): pass
    async def variable_ops(self, var_type, var_id, operation, **kwargs): raise NotImplementedError
    async def get_routine_summary(self, routine_id): raise NotImplementedError
    async def get_all_routines(self): raise NotImplementedError
    async def add_node(self, node_name, type): raise NotImplementedError
    async def node_ops(self, node_id, operation, **kwargs): raise NotImplementedError
    async def routine_ops(self, routine_id, operation): raise NotImplementedError
    def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    def group_scene_update_link(self, *a, **kw): raise NotImplementedError
    def group_scene_get_node_roles(self, *a, **kw): raise NotImplementedError
    def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError


CODE = 'if device("25 80 3C 1").status("ST", uom=17, precision=1) > 72:\n    device("BAR1").command("DON")'


@pytest.mark.asyncio
async def test_create_routine_compiles_and_resolves_id_by_name():
    backend = FakeBackend()
    result = await execute_tool(
        "create_or_update_routine", {"name": "Evening Routine", "code": CODE}, nucore_interface=backend
    )

    assert result == {"name": "Evening Routine", "id": 99, "status": "saved"}
    assert backend.created_trigger["name"] == "Evening Routine"
    assert "parent" not in backend.created_trigger and "enabled" not in backend.created_trigger
    assert backend.created_trigger["if"][0]["type"] == "status"
    assert backend.refresh_calls == 1


@pytest.mark.asyncio
async def test_bad_dsl_surfaces_compile_error_cleanly():
    backend = FakeBackend()
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Bad", "code": 'if device("A").status("ST") > 5:\n    pass'},
        nucore_interface=backend,
    )
    assert "error" in result and "uom and precision" in result["error"]


EXISTING_ROUTINE = {
    "id": 29,
    "name": "Evening Routine",
    "parent": 5,
    "if": [{"type": "control", "andOr": "and", "id": "DON", "node": "OLD", "op": "IS"}],
    "then": [{"type": "cmd", "id": "DOF", "node": "OLD", "p": []}],
    "else": [],
}


@pytest.mark.asyncio
async def test_update_routine_calls_update_not_create_and_carries_the_given_id():
    """An update (id given) must go through update_routine, never
    create_automation_routine, and must never trigger the
    create-path's refresh-then-search-by-name id lookup -- the id is
    already known."""
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "code": CODE},
        nucore_interface=backend,
    )

    assert result == {"name": "Evening Routine", "id": 29, "status": "saved"}
    assert backend.created_trigger is None
    assert backend.updated_trigger["id"] == 29
    assert backend.updated_trigger["name"] == "Evening Routine"
    assert backend.refresh_calls == 0


@pytest.mark.asyncio
async def test_update_routine_carries_the_existing_parent():
    """Confirmed: an update must carry the routine's existing `parent`
    (fetched fresh from the backend, not left to the model to remember) --
    the DSL has no way to express it, and it's not something the customer's
    request would ever mention."""
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE  # parent: 5
    await execute_tool(
        "create_or_update_routine", {"name": "Evening Routine", "id": 29, "code": CODE}, nucore_interface=backend
    )
    assert backend.updated_trigger["parent"] == 5


@pytest.mark.asyncio
async def test_update_routine_fails_cleanly_when_existing_routine_cant_be_fetched():
    """If the parent-fetch fails, the update must not proceed with a
    missing/wrong parent -- fail loudly instead."""
    backend = FakeBackend()
    backend.routine_detail = None  # get_routine will return a 404 FakeResp
    result = await execute_tool(
        "create_or_update_routine", {"name": "Evening Routine", "id": 29, "code": CODE}, nucore_interface=backend
    )
    assert "error" in result and "before update" in result["error"]
    assert backend.updated_trigger is None


@pytest.mark.asyncio
async def test_update_routine_backend_rejection_is_not_reported_as_saved():
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE
    backend.update_status = 400
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "code": CODE},
        nucore_interface=backend,
    )
    assert "error" in result and "HTTP 400" in result["error"]


@pytest.mark.asyncio
async def test_update_routine_rejection_surfaces_hub_error_message():
    """The hub's rejection body carries an AI-friendly explanation
    (errorCode/errorMessage) beyond the bare status code -- surface it so a
    repair turn can act on the actual reason, not just \"HTTP 400\"."""
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE
    backend.update_status = 400
    backend.update_error_body = {"successful": False, "data": None, "errorCode": "BadRequestError", "errorMessage": "Invalid program"}
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "code": CODE},
        nucore_interface=backend,
    )
    assert "error" in result
    assert "HTTP 400" in result["error"]
    assert "BadRequestError" in result["error"] and "Invalid program" in result["error"]


@pytest.mark.asyncio
async def test_update_routine_replaces_full_content_not_a_patch():
    """The compiler has no partial-update mode -- whatever `code` is
    supplied becomes the routine's ENTIRE if/then/else. A caller that
    fetched only a fragment of the current logic (or none at all) would
    silently drop the rest -- this test documents that the handler itself
    enforces no safety net here; the tool description's guidance to call
    get_routine_detail first is the only thing that prevents data loss."""
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE
    minimal_code = 'device("BAR1").command("DON")'
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "code": minimal_code},
        nucore_interface=backend,
    )
    assert result["status"] == "saved"
    assert backend.updated_trigger["if"] == []
    assert backend.updated_trigger["then"] == [{"type": "cmd", "id": "DON", "node": "BAR1", "p": []}]


@pytest.mark.asyncio
async def test_backend_rejection_is_not_reported_as_saved():
    backend = FakeBackend()
    backend.create_status = 400
    result = await execute_tool(
        "create_or_update_routine", {"name": "Evening Routine", "code": CODE}, nucore_interface=backend
    )
    assert "error" in result and "HTTP 400" in result["error"]


@pytest.mark.asyncio
async def test_create_routine_rejection_surfaces_hub_error_message():
    backend = FakeBackend()
    backend.create_status = 400
    backend.create_error_body = {"successful": False, "data": None, "errorCode": "BadRequestError", "errorMessage": "Invalid program"}
    result = await execute_tool(
        "create_or_update_routine", {"name": "Evening Routine", "code": CODE}, nucore_interface=backend
    )
    assert "error" in result
    assert "HTTP 400" in result["error"]
    assert "BadRequestError" in result["error"] and "Invalid program" in result["error"]


@pytest.mark.asyncio
async def test_get_routine_detail_returns_full_trigger():
    backend = FakeBackend()
    backend.routine_detail = {
        "id": 29,
        "name": "Movie Test",
        "parent": 0,
        "comment": "test",
        "if": [{"type": "status", "andOr": "and", "id": "ST", "node": "A", "op": "GT", "val": {"value": 1, "prec": 0, "uom": 25}}],
        "then": [{"type": "cmd", "id": "DON", "node": "BackyardSteps", "p": []}],
        "else": [],
    }
    result = await execute_tool("get_routine_detail", {"id": 29}, nucore_interface=backend)
    assert result["id"] == 29 and result["name"] == "Movie Test"
    assert result["if"][0]["type"] == "status"


@pytest.mark.asyncio
async def test_get_routine_detail_missing_routine():
    backend = FakeBackend()
    result = await execute_tool("get_routine_detail", {"id": 999}, nucore_interface=backend)
    assert "error" in result and "HTTP 404" in result["error"]


@pytest.mark.asyncio
async def test_get_routine_detail_requires_id():
    backend = FakeBackend()
    result = await execute_tool("get_routine_detail", {}, nucore_interface=backend)
    assert "error" in result


@pytest.mark.asyncio
async def test_get_routine_detail_annotates_var_condition_and_action_with_name():
    backend = FakeBackend()
    backend.variables = {
        "1:5": {"id": "5", "name": "Irrigation_Mode", "type": 1},
        "1:6": {"id": "6", "name": "Rain_Delay", "type": 1},
    }
    backend.routine_detail = {
        "id": 29,
        "name": "Watering",
        "parent": 0,
        "if": [
            {"type": "var", "andOr": "and", "id": 5, "varType": "1", "op": "GT", "val": {"value": 0, "prec": 0}},
        ],
        "then": [
            {"type": "var", "varType": "1", "id": 5, "op": "EQ", "var": {"id": 6, "type": "1"}},
        ],
        "else": [],
    }
    result = await execute_tool("get_routine_detail", {"id": 29}, nucore_interface=backend)
    assert result["if"][0]["name"] == "Irrigation_Mode"
    assert result["then"][0]["name"] == "Irrigation_Mode"
    assert result["then"][0]["var"]["name"] == "Rain_Delay"


@pytest.mark.asyncio
async def test_get_routine_detail_annotates_var_action_op_label_but_not_condition():
    """Real, observed model mistake: a `var` action's op="EQ" (an
    assignment) got narrated in English as "check if X equals 1" -- the
    wording for a `var` CONDITION (a comparison). op_label removes the
    ambiguity by translating the token deterministically; conditions never
    get one, since their own op vocabulary (GT/GE/LT/LE/IS/ISNOT) isn't in
    the action-only label table."""
    backend = FakeBackend()
    backend.variables = {"1:1": {"id": "1", "name": "Watering_the_plants", "type": 1}}
    backend.routine_detail = {
        "id": 9,
        "name": "Water the plants",
        "parent": 8,
        "if": [
            {"type": "var", "andOr": "and", "id": 1, "varType": "1", "op": "GT", "val": {"value": 0, "prec": 0}},
        ],
        "then": [
            {"type": "var", "varType": "1", "id": 1, "op": "EQ", "val": {"value": 1, "prec": 0}},
        ],
        "else": [],
    }
    result = await execute_tool("get_routine_detail", {"id": 9}, nucore_interface=backend)
    assert result["then"][0]["op_label"] == "set equal to"
    assert "op_label" not in result["if"][0]


@pytest.mark.asyncio
async def test_get_routine_detail_annotates_while_repeat_var_with_name():
    backend = FakeBackend()
    backend.variables = {"2:7": {"id": "7", "name": "Poolpump_has_already_run", "type": 2}}
    backend.routine_detail = {
        "id": 29,
        "name": "Pool",
        "parent": 0,
        "if": [],
        "then": [
            {"type": "repeat", "while": {"var": {"op": "IS", "varType": "2", "id": 7, "val": {"value": 1, "prec": 0}}}},
        ],
        "else": [],
    }
    result = await execute_tool("get_routine_detail", {"id": 29}, nucore_interface=backend)
    assert result["then"][0]["while"]["var"]["name"] == "Poolpump_has_already_run"


@pytest.mark.asyncio
async def test_get_routine_detail_var_condition_without_known_variable_gets_no_name():
    backend = FakeBackend()
    backend.variables = {}
    backend.routine_detail = {
        "id": 29,
        "name": "Watering",
        "parent": 0,
        "if": [{"type": "var", "andOr": "and", "id": 99, "varType": "1", "op": "GT", "val": {"value": 0, "prec": 0}}],
        "then": [],
        "else": [],
    }
    result = await execute_tool("get_routine_detail", {"id": 29}, nucore_interface=backend)
    assert "name" not in result["if"][0]

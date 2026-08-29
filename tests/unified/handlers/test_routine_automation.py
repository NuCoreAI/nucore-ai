"""End-to-end: create_or_update_routine dispatched through execute_tool,
compiling via the new unified.routine_compiler package and calling into a
fake NuCoreInterface backend -- confirms the whole chain (DSL -> Trigger
dict -> backend call -> id resolution) works together, not just the
compiler in isolation.
"""

from __future__ import annotations

import pytest

from nucore.cmd import Command, CommandParameter
from nucore.editor import Editor, EditorSubsetRange
from nucore.node import Node
from nucore.nodedef import NodeCommands, NodeDef, NodeProperty
from nucore.nucore_interface import NuCoreInterface
from nucore.uom import UOMEntry
from unified.dispatch import execute_tool

UOM25 = UOMEntry(id="25", description="Enum", label="Enum", name="Enum")
UOM146 = UOMEntry(id="146", description="Short Notification ID", label="Notification ID", name="Notification ID")


def _build_node(address: str, *, properties=None, accepts=None, sends=None) -> Node:
    """Minimal Node/NodeDef fixture -- enough for
    routine_automation._resolve_condition/_resolve_action to find a real
    property/command id (or confirm a given one is already real)."""
    node_def = NodeDef(
        id=f"{address}_profile",
        properties={p: NodeProperty(id=p, editor=None, name=p) for p in (properties or [])},
        cmds=NodeCommands(
            accepts=[Command(id=c, name=c) for c in (accepts or [])],
            sends=[Command(id=c, name=c) for c in (sends or [])],
        ),
    )
    node = object.__new__(Node)
    node.address = address
    node.name = address
    node.node_def = node_def
    return node


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
        self.nodes = {
            "25 80 3C 1": _build_node("25 80 3C 1", properties=["ST"]),
            "BAR1": _build_node("BAR1", accepts=["DON"]),
            "OLD": _build_node("OLD", accepts=["DOF"], sends=["DON"]),
        }
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
    async def run_diagnostic_step(self, step, **params): raise NotImplementedError
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError
    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self): raise NotImplementedError
    async def finish_device_discovery(self): raise NotImplementedError


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
async def test_command_and_property_display_names_resolve_to_real_ids():
    """The DSL now takes get_device_detail's display name for .status(...)/
    .command(...)/.was_controlled(...), not the raw id -- confirms the
    handler resolves "Status"/"On"/"Off" to the real "ST"/"DON"/"DOF"
    before saving, the actual fix for the bug where routines were created
    with names ("On"/"Off") sitting in the id field."""
    backend = FakeBackend()
    backend.nodes["53 65 12 1"] = _build_node(
        "53 65 12 1", properties=[], accepts=["DON"], sends=["DOF"]
    )
    backend.nodes["53 65 12 1"].node_def.cmds.accepts[0].name = "On"
    backend.nodes["53 65 12 1"].node_def.cmds.sends[0].name = "Off"
    backend.nodes["25 80 3C 1"].node_def.properties["ST"].name = "Status"

    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Name Resolution Test", "code": (
            'if device("25 80 3C 1").status("Status", uom=17, precision=1) > 72:\n'
            '    device("53 65 12 1").command("On")\n'
            'else:\n'
            '    device("53 65 12 1").command("On")'
        )},
        nucore_interface=backend,
    )

    assert result["status"] == "saved"
    assert backend.created_trigger["if"][0]["id"] == "ST"
    assert backend.created_trigger["then"][0]["id"] == "DON"
    assert backend.created_trigger["else"][0]["id"] == "DON"


@pytest.mark.asyncio
async def test_was_controlled_name_resolves_against_sends_not_accepts():
    backend = FakeBackend()
    backend.nodes["MOTION1"] = _build_node("MOTION1", accepts=["QUERY"], sends=["DON"])
    backend.nodes["MOTION1"].node_def.cmds.sends[0].name = "On"

    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Motion Test", "code": 'if device("MOTION1").was_controlled(command="On", eq="is"):\n    pass'},
        nucore_interface=backend,
    )

    assert result["status"] == "saved"
    assert backend.created_trigger["if"][0]["id"] == "DON"


@pytest.mark.asyncio
async def test_unknown_command_name_returns_a_clear_error_not_a_silent_save():
    backend = FakeBackend()
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Bad Command", "code": 'device("BAR1").command("Nonexistent Command")'},
        nucore_interface=backend,
    )
    assert "error" in result
    assert "Nonexistent Command" in result["error"]
    assert backend.created_trigger is None


@pytest.mark.asyncio
async def test_unknown_property_name_returns_a_clear_error_not_a_silent_save():
    backend = FakeBackend()
    result = await execute_tool(
        "create_or_update_routine",
        {
            "name": "Bad Property",
            "code": 'if device("25 80 3C 1").status("Not A Real Property", uom=17, precision=1) > 72:\n    pass',
        },
        nucore_interface=backend,
    )
    assert "error" in result
    assert "Not A Real Property" in result["error"]
    assert backend.created_trigger is None


@pytest.mark.asyncio
async def test_unknown_device_id_returns_a_clear_error():
    backend = FakeBackend()
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Bad Device", "code": 'device("NOT_A_REAL_DEVICE").command("On")'},
        nucore_interface=backend,
    )
    assert "error" in result
    assert "NOT_A_REAL_DEVICE" in result["error"]
    assert backend.created_trigger is None


@pytest.mark.asyncio
async def test_adjust_scene_command_name_resolves_against_responder_accepts():
    backend = FakeBackend()
    backend.nodes["RESPONDER1"] = _build_node("RESPONDER1", accepts=["DON"])
    backend.nodes["RESPONDER1"].node_def.cmds.accepts[0].name = "On"

    code = 'adjust_scene(group="G1", controller="CTL1", node="RESPONDER1", type="cmd", command="On")'
    result = await execute_tool(
        "create_or_update_routine", {"name": "Scene Adjust Test", "code": code}, nucore_interface=backend
    )

    assert result["status"] == "saved"
    assert backend.created_trigger["then"][0]["rsp"]["cmd"]["cmdId"] == "DON"


@pytest.mark.asyncio
async def test_command_param_enum_label_resolves_to_real_index_before_dispatch():
    """Real reported bug: the model wrote an enum parameter's label text
    (what it read next to the real index in get_device_detail's editor
    dict) as value= instead of the index -- the compiler now lets that
    label through unresolved (routine_compiler tests), and this confirms
    create_or_update_routine resolves it to the real index server-side,
    the same "backend does deterministic lookup" pattern as command/
    property name resolution just above."""
    backend = FakeBackend()
    sound_editor = Editor(
        id="I_SOUND", is_reference=False,
        ranges=[EditorSubsetRange(id="I_SOUND", uom=UOM146, subset="0-3",
                                   names={"1": "Clock Radio Alarm", "2": "Siren"})],
    )
    backend.nodes["n007_udmobile"] = _build_node("n007_udmobile", accepts=["GV10"])
    backend.nodes["n007_udmobile"].node_def.cmds.accepts[0].name = "Send Message"
    backend.nodes["n007_udmobile"].node_def.cmds.accepts[0].parameters = [
        CommandParameter(id="Sound", editor=sound_editor)
    ]

    code = (
        'device("n007_udmobile").command("Send Message", '
        'params=[param(id="Sound", value="Clock Radio Alarm", uom=146, precision=0)])'
    )
    result = await execute_tool("create_or_update_routine", {"name": "Alarm Test", "code": code}, nucore_interface=backend)

    assert result["status"] == "saved"
    assert backend.created_trigger["then"][0]["p"][0]["val"]["value"] == 1


@pytest.mark.asyncio
async def test_command_param_two_anonymous_params_resolve_positionally_to_different_editors():
    """The exact bug shape: two id="" params on one command, each with a
    different editor -- id-only matching can't tell them apart (both have
    the same empty id), so this must resolve positionally instead."""
    backend = FakeBackend()
    sound_editor = Editor(
        id="I_SOUND", is_reference=False,
        ranges=[EditorSubsetRange(id="I_SOUND", uom=UOM146, subset="0-3", names={"1": "Clock Radio Alarm"})],
    )
    content_editor = Editor(
        id="I_CONTENT", is_reference=False,
        ranges=[EditorSubsetRange(id="I_CONTENT", uom=UOM146, subset="0-15", names={"11": "General Notifications"})],
    )
    backend.nodes["n007_udmobile"] = _build_node("n007_udmobile", accepts=["GV10"])
    backend.nodes["n007_udmobile"].node_def.cmds.accepts[0].name = "Send Message"
    backend.nodes["n007_udmobile"].node_def.cmds.accepts[0].parameters = [
        CommandParameter(id="", editor=sound_editor),
        CommandParameter(id="", editor=content_editor),
    ]

    code = (
        'device("n007_udmobile").command("Send Message", params=['
        'param(id="", value="Clock Radio Alarm", uom=146, precision=0), '
        'param(id="", value="General Notifications", uom=146, precision=0)])'
    )
    result = await execute_tool("create_or_update_routine", {"name": "Alarm Test", "code": code}, nucore_interface=backend)

    assert result["status"] == "saved"
    params = backend.created_trigger["then"][0]["p"]
    assert params[0]["val"]["value"] == 1
    assert params[1]["val"]["value"] == 11


@pytest.mark.asyncio
async def test_status_condition_enum_label_resolves_to_real_index_before_dispatch():
    backend = FakeBackend()
    mode_editor = Editor(
        id="I_MODE", is_reference=False,
        ranges=[EditorSubsetRange(id="I_MODE", uom=UOM25, subset="0-1", names={"0": "Off", "1": "On"})],
    )
    backend.nodes["25 80 3C 1"].node_def.properties["ST"] = NodeProperty(id="ST", editor=mode_editor, name="Status")

    code = 'if device("25 80 3C 1").status("ST", uom=25, precision=0) == "On":\n    device("BAR1").command("DON")'
    result = await execute_tool("create_or_update_routine", {"name": "Mode Test", "code": code}, nucore_interface=backend)

    assert result["status"] == "saved"
    assert backend.created_trigger["if"][0]["val"]["value"] == 1


@pytest.mark.asyncio
async def test_unresolvable_enum_label_returns_a_clear_error_not_a_silent_save():
    backend = FakeBackend()
    sound_editor = Editor(
        id="I_SOUND", is_reference=False,
        ranges=[EditorSubsetRange(id="I_SOUND", uom=UOM146, subset="0-3", names={"1": "Clock Radio Alarm"})],
    )
    backend.nodes["n007_udmobile"] = _build_node("n007_udmobile", accepts=["GV10"])
    backend.nodes["n007_udmobile"].node_def.cmds.accepts[0].name = "Send Message"
    backend.nodes["n007_udmobile"].node_def.cmds.accepts[0].parameters = [
        CommandParameter(id="Sound", editor=sound_editor)
    ]

    code = (
        'device("n007_udmobile").command("Send Message", '
        'params=[param(id="Sound", value="Not A Real Sound", uom=146, precision=0)])'
    )
    result = await execute_tool("create_or_update_routine", {"name": "Bad Enum", "code": code}, nucore_interface=backend)

    assert "error" in result
    assert "Not A Real Sound" in result["error"]
    assert backend.created_trigger is None


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

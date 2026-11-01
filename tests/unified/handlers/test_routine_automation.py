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
from unified.handlers import routine_automation as routine_automation_module

UOM25 = UOMEntry(id="25", description="Enum", label="Enum", name="Enum")
UOM146 = UOMEntry(id="146", description="Short Notification ID", label="Notification ID", name="Notification ID")


@pytest.fixture(autouse=True)
def _fast_routine_wait_timeout(monkeypatch):
    # A created routine's id lookup (_wait_for_routine_id_by_name) waits --
    # event-driven, via wait_until -- for a real _1 event or for the given
    # name to show up in condensed_routines. FakeBackend's
    # _refresh_routines_database always appends a fixed "Evening Routine"
    # entry, so any test creating a routine under a different name would
    # otherwise block for the real default budget with no event ever
    # arriving and the name never matching -- shrink it so those tests (and
    # any real timeout tests) stay fast.
    monkeypatch.setattr(routine_automation_module, "_ROUTINE_CREATE_WAIT_TIMEOUT_S", 0.05)


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
        self.routine_details_by_id: dict = {}
        self.routine_summary_by_id: dict = {}

    async def create_automation_routine(self, trigger):
        self.created_trigger = trigger
        return FakeResp(self.create_status, self.create_error_body)

    async def update_routine(self, program):
        self.updated_trigger = program
        return FakeResp(self.update_status, self.update_error_body)

    async def get_routine(self, routine_id):
        if routine_id in self.routine_details_by_id:
            return self.routine_details_by_id[routine_id]
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

    async def get_routine_summary(self, routine_id):
        if routine_id in self.routine_summary_by_id:
            return self.routine_summary_by_id[routine_id]
        raise NotImplementedError
    async def get_all_routines(self): raise NotImplementedError
    async def add_node(self, node_name, type): raise NotImplementedError
    async def node_ops(self, node_id, operation, **kwargs): raise NotImplementedError
    async def routine_ops(self, routine_id, operation): raise NotImplementedError
    async def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    async def group_scene_update_link(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_node_roles(self, *a, **kw): raise NotImplementedError
    async def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def scene_test(self, device_id): raise NotImplementedError
    async def run_diagnostic_step(self, step, **params): raise NotImplementedError
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError
    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self): raise NotImplementedError
    async def finish_device_discovery(self): raise NotImplementedError
    async def remove_device(self, device_address, protocol=None, **kwargs): raise NotImplementedError


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


FOLDER_CODE = 'if device("25 80 3C 1").status("ST", uom=17, precision=1) > 72:\n    pass'


@pytest.mark.asyncio
async def test_create_folder_sets_isfolder_with_no_then_else_and_resolves_condition():
    backend = FakeBackend()
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "code": FOLDER_CODE, "is_folder": True},
        nucore_interface=backend,
    )

    assert result == {"name": "Evening Routine", "id": 99, "status": "saved"}
    assert backend.created_trigger["isFolder"] is True
    assert "then" not in backend.created_trigger and "else" not in backend.created_trigger
    # Condition resolution still ran over `if` for a folder, same as a routine.
    assert backend.created_trigger["if"][0]["type"] == "status"
    assert backend.created_trigger["if"][0]["id"] == "ST"


@pytest.mark.asyncio
async def test_is_folder_is_ignored_on_update():
    """Folder-ness is only settable on create -- an update must never be able
    to turn an existing routine into a folder (or vice versa) via a stray
    is_folder argument, the same "never let an unrelated edit change
    placement/identity metadata" reasoning as the existing parent force-fetch."""
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "code": CODE, "is_folder": True},
        nucore_interface=backend,
    )

    assert result == {"name": "Evening Routine", "id": 29, "status": "saved"}
    assert "isFolder" not in backend.updated_trigger
    assert "then" in backend.updated_trigger and "else" in backend.updated_trigger


@pytest.mark.asyncio
async def test_create_routine_inside_a_folder_sets_parent_from_folder_id():
    backend = FakeBackend()
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "code": CODE, "folder_id": 5},
        nucore_interface=backend,
    )

    assert result == {"name": "Evening Routine", "id": 99, "status": "saved"}
    assert backend.created_trigger["parent"] == 5


@pytest.mark.asyncio
async def test_create_routine_without_folder_id_is_unchanged_root_creation():
    backend = FakeBackend()
    result = await execute_tool(
        "create_or_update_routine", {"name": "Evening Routine", "code": CODE}, nucore_interface=backend
    )

    assert result == {"name": "Evening Routine", "id": 99, "status": "saved"}
    assert "parent" not in backend.created_trigger


@pytest.mark.asyncio
async def test_folder_id_combined_with_existing_id_is_rejected():
    """Ambiguous with the (not yet implemented) move mechanism -- folder_id
    only ever places a brand-new routine, never repositions an existing one."""
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "code": CODE, "folder_id": 5},
        nucore_interface=backend,
    )

    assert "error" in result
    assert backend.created_trigger is None and backend.updated_trigger is None


@pytest.mark.asyncio
async def test_update_with_move_to_folder_id_overrides_the_existing_parent():
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE  # parent: 5
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "code": CODE, "move_to_folder_id": 12},
        nucore_interface=backend,
    )

    assert result == {"name": "Evening Routine", "id": 29, "status": "saved"}
    assert backend.updated_trigger["parent"] == 12


@pytest.mark.asyncio
async def test_update_without_move_to_folder_id_key_preserves_existing_parent():
    """The key regression test: an ordinary content edit that never
    mentions move_to_folder_id at all must leave `parent` exactly as
    force-fetched -- proves this doesn't reopen the accidental-detachment
    footgun the force-fetch exists to prevent."""
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE  # parent: 5
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "code": CODE},
        nucore_interface=backend,
    )

    assert result == {"name": "Evening Routine", "id": 29, "status": "saved"}
    assert backend.updated_trigger["parent"] == 5


@pytest.mark.asyncio
async def test_update_with_explicit_falsy_move_to_folder_id_is_still_honored():
    """A falsy-but-explicit folder id (0) is a real, meaningful destination --
    must be distinguished from "key not supplied", not treated as absent.
    (The real root folder is id 1, "My Programs", not 0 -- this test only
    confirms the presence-vs-truthiness code path, it isn't claiming 0 means
    root; see tool_routine_create_or_update.json's own note on this.)"""
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE  # parent: 5
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "code": CODE, "move_to_folder_id": 0},
        nucore_interface=backend,
    )

    assert result == {"name": "Evening Routine", "id": 29, "status": "saved"}
    assert backend.updated_trigger["parent"] == 0


@pytest.mark.asyncio
async def test_update_with_move_to_folder_id_one_moves_to_my_programs_root():
    """The real root folder is id 1 ("My Programs"), confirmed against a
    live hub -- this is the id the model is actually told to use for root,
    not 0."""
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE  # parent: 5
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "code": CODE, "move_to_folder_id": 1},
        nucore_interface=backend,
    )

    assert result == {"name": "Evening Routine", "id": 29, "status": "saved"}
    assert backend.updated_trigger["parent"] == 1


@pytest.mark.asyncio
async def test_move_only_with_no_code_leaves_logic_byte_for_byte_unchanged():
    """The actual bug report this fixes: a move was previously indistinguishable
    from a full content rewrite, since `code` was unconditionally required --
    forcing the caller to reconstruct (and risk altering) if/then/else just to
    relocate a routine. move_to_folder_id alone, with code omitted entirely,
    must resubmit the routine's own existing content verbatim with only
    `parent` changed."""
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE  # parent: 5
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "move_to_folder_id": 12},
        nucore_interface=backend,
    )

    assert result == {"name": "Evening Routine", "id": 29, "status": "saved"}
    assert backend.created_trigger is None
    assert backend.updated_trigger == {**EXISTING_ROUTINE, "parent": 12}


@pytest.mark.asyncio
async def test_move_only_marks_routines_changed_for_the_next_refresh():
    """The actual bug report: without this, condensed_routines/ROUTINES
    DATABASE kept showing a routine's old folder/content until the hub's own
    _1 websocket event happened to arrive and get processed first -- a move
    (or any other update) has no self-healing fallback the way CREATE does."""
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE
    backend.routines_changed = False
    await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "move_to_folder_id": 12},
        nucore_interface=backend,
    )

    assert backend.routines_changed is True


@pytest.mark.asyncio
async def test_move_only_fails_cleanly_when_existing_routine_cant_be_fetched():
    backend = FakeBackend()
    backend.routine_detail = None  # get_routine will return a 404 FakeResp
    result = await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "move_to_folder_id": 12},
        nucore_interface=backend,
    )

    assert "error" in result
    assert backend.updated_trigger is None


@pytest.mark.asyncio
async def test_no_code_and_no_move_to_folder_id_still_requires_code():
    """Omitting code is ONLY valid for a pure move -- an ordinary update or
    create call without code must still be rejected, same as before."""
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE
    result = await execute_tool(
        "create_or_update_routine", {"name": "Evening Routine", "id": 29}, nucore_interface=backend
    )

    assert "error" in result
    assert backend.updated_trigger is None


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
async def test_ordinary_content_edit_update_marks_routines_changed_for_the_next_refresh():
    """Same bug as the move-only case, for a plain content edit: an UPDATE
    has no self-healing fallback the way CREATE's _wait_for_routine_id_by_name
    does, so without this, a content edit's effect stays invisible to
    condensed_routines/ROUTINES DATABASE until the hub's own _1 event."""
    backend = FakeBackend()
    backend.routine_detail = EXISTING_ROUTINE
    backend.routines_changed = False
    await execute_tool(
        "create_or_update_routine",
        {"name": "Evening Routine", "id": 29, "code": CODE},
        nucore_interface=backend,
    )

    assert backend.routines_changed is True


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
    get_routine_details first is the only thing that prevents data loss."""
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
async def test_get_routine_details_returns_full_trigger():
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
    result = await execute_tool("get_routine_details", {"ids": [29]}, nucore_interface=backend)
    assert result[0]["id"] == 29 and result[0]["name"] == "Movie Test"
    assert result[0]["if"][0]["type"] == "status"


@pytest.mark.asyncio
async def test_get_routine_details_missing_routine():
    backend = FakeBackend()
    result = await execute_tool("get_routine_details", {"ids": [999]}, nucore_interface=backend)
    assert "error" in result[0] and "HTTP 404" in result[0]["error"]


@pytest.mark.asyncio
async def test_get_routine_details_attaches_running_state_matched_by_id():
    """get_routine_summary's response includes folder/ancestor entries
    alongside the target program -- the right one must be picked out by id,
    not assumed to be first in the list."""
    backend = FakeBackend()
    backend.routine_detail = {"id": 29, "name": "Movie Test", "parent": 0, "if": [], "then": [], "else": []}
    backend.routine_summary_by_id = {
        29: [
            {"id": 1, "name": "My Programs", "folder": True},
            {
                "id": 29, "name": "Movie Test", "folder": False, "status": True, "enabled": True,
                "runAtStartup": False, "running": False,
                "lastRunTime": "2026-09-15 20:00:00", "lastFinishTime": "2026-09-15 20:00:05",
                "nextScheduledRunTime": "2026-09-16 20:00:00",
            },
        ]
    }
    result = await execute_tool("get_routine_details", {"ids": [29]}, nucore_interface=backend)
    assert result[0]["running_state"]["id"] == 29
    assert result[0]["running_state"]["status"] is True
    assert result[0]["running_state"]["lastRunTime"] == "2026-09-15 20:00:00"


@pytest.mark.asyncio
async def test_get_routine_details_omits_running_state_when_summary_unavailable():
    """Runtime-summary failure is best-effort -- it must not fail the whole
    call, since the if/then/else logic is what this tool primarily exists
    for (FakeBackend's default get_routine_summary raises NotImplementedError,
    same as an unmocked/unavailable hub endpoint would surface as an
    exception)."""
    backend = FakeBackend()
    backend.routine_detail = {"id": 29, "name": "Movie Test", "parent": 0, "if": [], "then": [], "else": []}
    result = await execute_tool("get_routine_details", {"ids": [29]}, nucore_interface=backend)
    assert result[0]["id"] == 29
    assert "running_state" not in result[0]


@pytest.mark.asyncio
async def test_get_routine_details_requires_ids():
    backend = FakeBackend()
    for bad_args in ({}, {"ids": []}, {"ids": 29}):
        result = await execute_tool("get_routine_details", bad_args, nucore_interface=backend)
        assert "error" in result


@pytest.mark.asyncio
async def test_get_routine_details_batches_multiple_ids_with_partial_failure():
    """The whole point of taking a list: several routines confirmed in one
    call instead of one round trip per id -- including when some of them
    don't exist, which shouldn't fail the ones that do."""
    backend = FakeBackend()
    backend.routine_details_by_id = {
        29: {"id": 29, "name": "Movie Test", "parent": 0, "if": [], "then": [], "else": []},
        999: FakeResp(404),
    }
    result = await execute_tool("get_routine_details", {"ids": [29, 999]}, nucore_interface=backend)
    assert len(result) == 2
    assert result[0]["id"] == 29 and result[0]["name"] == "Movie Test"
    assert result[1]["id"] == 999 and "HTTP 404" in result[1]["error"]


@pytest.mark.asyncio
async def test_get_routine_details_annotates_var_condition_and_action_with_name():
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
    result = await execute_tool("get_routine_details", {"ids": [29]}, nucore_interface=backend)
    assert result[0]["if"][0]["name"] == "Irrigation_Mode"
    assert result[0]["then"][0]["name"] == "Irrigation_Mode"
    assert result[0]["then"][0]["var"]["name"] == "Rain_Delay"


@pytest.mark.asyncio
async def test_get_routine_details_annotates_var_action_op_label_but_not_condition():
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
    result = await execute_tool("get_routine_details", {"ids": [9]}, nucore_interface=backend)
    assert result[0]["then"][0]["op_label"] == "set equal to"
    assert "op_label" not in result[0]["if"][0]


@pytest.mark.asyncio
async def test_get_routine_details_annotates_while_repeat_var_with_name():
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
    result = await execute_tool("get_routine_details", {"ids": [29]}, nucore_interface=backend)
    assert result[0]["then"][0]["while"]["var"]["name"] == "Poolpump_has_already_run"


@pytest.mark.asyncio
async def test_get_routine_details_var_condition_without_known_variable_gets_no_name():
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
    result = await execute_tool("get_routine_details", {"ids": [29]}, nucore_interface=backend)
    assert "name" not in result[0]["if"][0]

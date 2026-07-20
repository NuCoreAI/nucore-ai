"""get_routine_detail must annotate index/enum-uom values (25/146/148) with
their real label, resolved server-side from the referenced device/command/
property's live Editor -- not leave the model to notice a raw index needs
translating and remember to make a follow-up get_device_detail call (it
didn't, in a real query: asked to explain a routine, it reported raw enum
indices as if they were meaningful numbers).

Mirrors a real reported case exactly: a "GV10" notify command with
Group/Sound/Content enum parameters on a UD Mobile-like device.
"""

from __future__ import annotations

import pytest

from nucore.cmd import Command, CommandParameter
from nucore.editor import Editor, EditorSubsetRange
from nucore.node import Node
from nucore.nodedef import NodeCommands, NodeDef
from nucore.nucore_interface import NuCoreInterface
from nucore.uom import UOMEntry
from unified.dispatch import execute_tool

UOM25 = UOMEntry(id="25", description="Enum", label="Enum", name="Enum")
UOM148 = UOMEntry(id="148", description="Notification content", label="Enum", name="Enum")
UOM_PCT = UOMEntry(id="51", description="Percent", label="%", name="%")


def _build_udmobile_node() -> Node:
    sound_editor = Editor(
        id="I_SOUND",
        is_reference=False,
        ranges=[EditorSubsetRange(id="I_SOUND", uom=UOM25, subset="0-3",
                                   names={"0": "None", "1": "Clock Radio Alarm", "2": "Siren", "3": "Chime"})],
    )
    content_editor = Editor(
        id="I_CONTENT",
        is_reference=False,
        ranges=[EditorSubsetRange(id="I_CONTENT", uom=UOM148, subset="0-15", names={"11": "General Notification"})],
    )
    group_editor = Editor(
        id="I_GROUP", is_reference=False, ranges=[EditorSubsetRange(id="I_GROUP", uom=UOM25, subset="0", names={"0": "All"})]
    )
    gv10 = Command(
        id="GV10",
        name="Notify",
        parameters=[
            CommandParameter(id="Group", editor=group_editor),
            CommandParameter(id="Sound", editor=sound_editor),
            CommandParameter(id="Content", editor=content_editor),
        ],
    )
    node_def = NodeDef(id="UDMobile", properties={}, cmds=NodeCommands(accepts=[gv10], sends=[]))
    node = object.__new__(Node)
    node.address = "n007_udmobile"
    node.name = "UD Mobile"
    node.node_def = node_def
    return node


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        udmobile = _build_udmobile_node()
        self.nodes = {udmobile.address: udmobile}
        self.groups = {}
        self.folders = {}
        self.routine_data = None

    async def get_routine(self, routine_id):
        return self.routine_data

    async def _load(self, **kwargs): raise NotImplementedError
    async def _load_routines(self): raise NotImplementedError
    async def send_commands(self, commands): raise NotImplementedError
    async def create_automation_routine(self, trigger): raise NotImplementedError
    async def update_routine(self, program): raise NotImplementedError
    async def get_properties(self, device_id): raise NotImplementedError
    def get_device_name(self, device_id): raise NotImplementedError
    def get_device_id(self, device_str): raise NotImplementedError
    async def get_all_routines_summary(self): raise NotImplementedError
    async def get_routine_summary(self, routine_id): raise NotImplementedError
    async def get_all_routines(self): raise NotImplementedError
    async def add_node(self, node_name, type): raise NotImplementedError
    async def node_ops(self, node_id, operation, **kwargs): raise NotImplementedError
    async def routine_ops(self, routine_id, operation): raise NotImplementedError
    async def _load_variables(self): pass
    async def variable_ops(self, var_type, var_id, operation, **kwargs): raise NotImplementedError
    def group_scene_add_member(self, *a, **kw): raise NotImplementedError
    def group_scene_remove_member(self, *a, **kw): raise NotImplementedError
    def group_scene_update_link(self, *a, **kw): raise NotImplementedError
    def group_scene_get_node_roles(self, *a, **kw): raise NotImplementedError
    def group_scene_get_link_types(self, *a, **kw): raise NotImplementedError
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError


@pytest.mark.asyncio
async def test_enum_uom_action_params_get_real_labels():
    backend = FakeBackend()
    backend.routine_data = {
        "id": 29,
        "name": "Movie Test",
        "parent": 1,
        "if": [],
        "then": [
            {
                "type": "cmd",
                "id": "DON",
                "node": "33 3A 1D 1",
                "p": [{"type": "val", "id": "", "val": {"uom": 51, "prec": 0, "value": 100}}],
            },
            {
                "type": "cmd",
                "id": "GV10",
                "node": "n007_udmobile",
                "p": [
                    {"type": "val", "id": "Group", "val": {"uom": 25, "prec": 0, "value": 0}},
                    {"type": "val", "id": "Sound", "val": {"uom": 25, "prec": 0, "value": 1}},
                    {"type": "val", "id": "Content", "val": {"uom": 148, "prec": 0, "value": 11}},
                ],
            },
        ],
        "else": [],
    }

    result = await execute_tool("get_routine_detail", {"id": 29}, nucore_interface=backend)

    gv10_params = result["then"][1]["p"]
    assert gv10_params[0]["val"]["label"] == "All"
    assert gv10_params[1]["val"]["label"] == "Clock Radio Alarm"
    assert gv10_params[2]["val"]["label"] == "General Notification"

    # non-enum uom (51 = percent) must not get a spurious label
    assert "label" not in result["then"][0]["p"][0]["val"]


@pytest.mark.asyncio
async def test_status_condition_enum_gets_a_label():
    from nucore.nodedef import NodeProperty

    backend = FakeBackend()
    mode_editor = Editor(
        id="I_MODE", is_reference=False,
        ranges=[EditorSubsetRange(id="I_MODE", uom=UOM25, subset="0-1", names={"0": "Off", "1": "On"})],
    )
    node = backend.nodes["n007_udmobile"]
    node.node_def.properties["ST"] = NodeProperty(id="ST", editor=mode_editor, name="Status")

    backend.routine_data = {
        "id": 30,
        "name": "Test",
        "parent": 0,
        "if": [{"type": "status", "andOr": "and", "id": "ST", "node": "n007_udmobile", "op": "IS",
                "val": {"value": 1, "prec": 0, "uom": 25}}],
        "then": [],
        "else": [],
    }

    result = await execute_tool("get_routine_detail", {"id": 30}, nucore_interface=backend)
    assert result["if"][0]["val"]["label"] == "On"


@pytest.mark.asyncio
async def test_paren_nested_status_condition_gets_a_label():
    from nucore.nodedef import NodeProperty

    backend = FakeBackend()
    mode_editor = Editor(
        id="I_MODE", is_reference=False,
        ranges=[EditorSubsetRange(id="I_MODE", uom=UOM25, subset="0-1", names={"0": "Off", "1": "On"})],
    )
    node = backend.nodes["n007_udmobile"]
    node.node_def.properties["ST"] = NodeProperty(id="ST", editor=mode_editor, name="Status")

    backend.routine_data = {
        "id": 31,
        "name": "Test",
        "parent": 0,
        "if": [{"type": "paren", "andOr": "and", "conditions": [
            {"type": "status", "andOr": "and", "id": "ST", "node": "n007_udmobile", "op": "IS",
             "val": {"value": 1, "prec": 0, "uom": 25}},
        ]}],
        "then": [],
        "else": [],
    }

    result = await execute_tool("get_routine_detail", {"id": 31}, nucore_interface=backend)
    assert result["if"][0]["conditions"][0]["val"]["label"] == "On"

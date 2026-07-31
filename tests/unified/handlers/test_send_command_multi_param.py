"""send_command must support commands that take more than one parameter (e.g.
a "Send Message" notification command needing a sound enum plus free-text
message content) instead of unconditionally refusing them.

Mirrors a real reported case: the model called `send_command` for UD
Mobile's "Send Message" command with only one of its two required values,
because the tool previously had no way to carry more than one.
"""

from __future__ import annotations

import pytest

from nucore.cmd import Command, CommandParameter
from nucore.editor import Editor, EditorMinMaxRange, EditorSubsetRange
from nucore.node import Node
from nucore.nodedef import NodeCommands, NodeDef
from nucore.nucore_interface import NuCoreInterface
from nucore.uom import UOMEntry
from unified.dispatch import execute_tool

UOM25 = UOMEntry(id="25", description="Enum", label="Enum", name="Enum")
UOM_RAW = UOMEntry(id="56", description="Raw", label="raw", name="raw")


def _build_node() -> Node:
    sound_editor = Editor(
        id="I_SOUND",
        is_reference=False,
        ranges=[EditorSubsetRange(id="I_SOUND", uom=UOM25, subset="0-3",
                                   names={"0": "None", "1": "Clock Radio Alarm", "2": "Siren", "3": "Chime"})],
    )
    # Free-text content parameter -- no enum/numeric editor at all.
    content_editor = Editor(id="I_CONTENT", is_reference=False, ranges=[])
    level_editor = Editor(
        id="I_LEVEL", is_reference=False,
        ranges=[EditorMinMaxRange(id="I_LEVEL", uom=UOM_RAW, min=0, max=100, prec=0)],
    )

    send_message = Command(
        id="SEND_MSG",
        name="Send Message",
        parameters=[
            CommandParameter(id="Sound", name="Sound", editor=sound_editor),
            CommandParameter(id="Content", name="Content", editor=content_editor),
        ],
    )
    optional_tail = Command(
        id="OPT",
        name="Optional Tail",
        parameters=[
            CommandParameter(id="Level", name="Level", editor=level_editor),
            CommandParameter(id="Extra", name="Extra", editor=level_editor, optional=True),
        ],
    )
    single = Command(id="DON", name="On", parameters=[])

    node_def = NodeDef(id="UDMobile", properties={}, cmds=NodeCommands(accepts=[send_message, optional_tail, single], sends=[]))
    node = object.__new__(Node)
    node.address = "n007_udmobile"
    node.name = "UD Mobile"
    node.node_def = node_def
    return node


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        udmobile = _build_node()
        self.nodes = {udmobile.address: udmobile}
        self.groups = {}
        self.folders = {}
        self.sent_commands: list = []

    async def send_commands(self, commands):
        self.sent_commands.append(commands)

    async def _load(self, **kwargs): raise NotImplementedError
    async def _load_routines(self): raise NotImplementedError
    async def create_automation_routine(self, trigger): raise NotImplementedError
    async def update_routine(self, program): raise NotImplementedError
    async def get_properties(self, device_id): raise NotImplementedError
    def get_device_name(self, device_id): raise NotImplementedError
    def get_device_id(self, device_str): raise NotImplementedError
    async def get_all_routines_summary(self): raise NotImplementedError
    async def get_routine_summary(self, routine_id): raise NotImplementedError
    async def get_all_routines(self): raise NotImplementedError
    async def get_routine(self, routine_id): raise NotImplementedError
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
    async def start_diagnostics(self, **kwargs): raise NotImplementedError
    async def run_diagnostic_step(self, step, **params): raise NotImplementedError
    def get_running_diagnostic(self): return None
    async def _subscribe_events(self, *a, **kw): raise NotImplementedError
    async def add_device(self, device_address, **kwargs): raise NotImplementedError
    async def discover_devices(self): raise NotImplementedError
    async def finish_device_discovery(self): raise NotImplementedError


@pytest.mark.asyncio
async def test_multi_param_command_resolves_each_value_in_order():
    backend = FakeBackend()
    result = await execute_tool(
        "send_command",
        {
            "device_id": "n007_udmobile",
            "command": "Send Message",
            "values": [{"value": "Clock Radio Alarm"}, {"value": "garage door"}],
        },
        nucore_interface=backend,
    )

    assert result["status"] == "sent"
    sent = backend.sent_commands[0][0]
    assert sent["command"] == "SEND_MSG"
    assert sent["parameters"] == [
        {"id": "Sound", "value": "1", "uom": 25, "precision": 0},
        {"id": "Content", "value": "garage door", "uom": 0, "precision": 0},
    ]


@pytest.mark.asyncio
async def test_multi_param_command_wrong_count_errors():
    backend = FakeBackend()
    result = await execute_tool(
        "send_command",
        {"device_id": "n007_udmobile", "command": "Send Message", "values": [
            {"value": "Clock Radio Alarm"}, {"value": "a"}, {"value": "b"},
        ]},
        nucore_interface=backend,
    )
    assert "error" in result
    assert not backend.sent_commands


@pytest.mark.asyncio
async def test_single_param_backward_compatible_value_field_still_works():
    backend = FakeBackend()
    result = await execute_tool(
        "send_command",
        {"device_id": "n007_udmobile", "command": "Optional Tail", "value": 42},
        nucore_interface=backend,
    )
    assert result["status"] == "sent"
    sent = backend.sent_commands[0][0]
    # Trailing optional parameter omitted entirely, not invented.
    assert sent["parameters"] == [{"id": "Level", "value": 42, "uom": 56, "precision": 0}]


@pytest.mark.asyncio
async def test_missing_required_trailing_value_errors():
    backend = FakeBackend()
    result = await execute_tool(
        "send_command",
        {"device_id": "n007_udmobile", "command": "Send Message", "values": [{"value": "Chime"}]},
        nucore_interface=backend,
    )
    assert "error" in result
    assert not backend.sent_commands


@pytest.mark.asyncio
async def test_zero_param_command_unaffected():
    backend = FakeBackend()
    result = await execute_tool(
        "send_command", {"device_id": "n007_udmobile", "command": "On"}, nucore_interface=backend
    )
    assert result["status"] == "sent"
    assert backend.sent_commands[0][0]["parameters"] == []

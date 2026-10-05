"""send_command must support commands that take more than one parameter (e.g.
a "Send Message" notification command needing a sound enum plus free-text
message content) instead of unconditionally refusing them, and must support
sending multiple commands -- to the same device or different ones -- in a
single call.

Mirrors a real reported case: the model called `send_command` for UD
Mobile's "Send Message" command with only one of its two required values,
because the tool previously had no way to carry more than one.
"""

from __future__ import annotations

from types import SimpleNamespace

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


def _build_udmobile_node() -> Node:
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


def _build_lamp_node() -> Node:
    node_def = NodeDef(id="Lamp", properties={}, cmds=NodeCommands(accepts=[Command(id="DON", name="On", parameters=[])], sends=[]))
    node = object.__new__(Node)
    node.address = "n008_lamp"
    node.name = "Lamp"
    node.node_def = node_def
    return node


class FakeBackend(NuCoreInterface):
    def __init__(self):
        super().__init__(json_output=True, formatter_type="minimal")
        udmobile = _build_udmobile_node()
        lamp = _build_lamp_node()
        self.nodes = {udmobile.address: udmobile, lamp.address: lamp}
        self.groups = {}
        self.folders = {}
        self.sent_commands: list = []
        # Test knobs -- None means "behave normally" (return one 200 per entry).
        self.raise_on_send: Exception | None = None
        self.response_status_codes: list[int] | None = None

    async def send_commands(self, commands):
        self.sent_commands.append(commands)
        if self.raise_on_send is not None:
            raise self.raise_on_send
        if self.response_status_codes is not None:
            return [SimpleNamespace(status_code=code) for code in self.response_status_codes]
        return [SimpleNamespace(status_code=200) for _ in commands]

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


@pytest.mark.asyncio
async def test_multi_param_command_resolves_each_value_in_order():
    backend = FakeBackend()
    result = await execute_tool(
        "send_command",
        {"commands": [{
            "device_id": "n007_udmobile",
            "command": "Send Message",
            "values": [{"value": "Clock Radio Alarm"}, {"value": "garage door"}],
        }]},
        nucore_interface=backend,
    )

    assert result["summary"] == {"total": 1, "successful": 1, "failed": 0}
    assert "error" not in result
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
        {"commands": [{"device_id": "n007_udmobile", "command": "Send Message", "values": [
            {"value": "Clock Radio Alarm"}, {"value": "a"}, {"value": "b"},
        ]}]},
        nucore_interface=backend,
    )
    assert "error" in result
    assert result["summary"] == {"total": 1, "successful": 0, "failed": 1}
    assert result["results"][0]["successful"] is False
    assert not backend.sent_commands


@pytest.mark.asyncio
async def test_single_value_field_shorthand_still_works_inside_one_entry():
    backend = FakeBackend()
    result = await execute_tool(
        "send_command",
        {"commands": [{"device_id": "n007_udmobile", "command": "Optional Tail", "value": 42}]},
        nucore_interface=backend,
    )
    assert result["summary"] == {"total": 1, "successful": 1, "failed": 0}
    sent = backend.sent_commands[0][0]
    # Trailing optional parameter omitted entirely, not invented.
    assert sent["parameters"] == [{"id": "Level", "value": 42, "uom": 56, "precision": 0}]


@pytest.mark.asyncio
async def test_missing_required_trailing_value_errors():
    backend = FakeBackend()
    result = await execute_tool(
        "send_command",
        {"commands": [{"device_id": "n007_udmobile", "command": "Send Message", "values": [{"value": "Chime"}]}]},
        nucore_interface=backend,
    )
    assert "error" in result
    assert not backend.sent_commands


@pytest.mark.asyncio
async def test_zero_param_command_unaffected():
    backend = FakeBackend()
    result = await execute_tool(
        "send_command", {"commands": [{"device_id": "n007_udmobile", "command": "On"}]}, nucore_interface=backend
    )
    assert result["summary"] == {"total": 1, "successful": 1, "failed": 0}
    assert backend.sent_commands[0][0]["parameters"] == []


@pytest.mark.asyncio
async def test_multiple_commands_in_one_call_all_succeed():
    backend = FakeBackend()
    result = await execute_tool(
        "send_command",
        {"commands": [
            {"device_id": "n007_udmobile", "command": "On"},
            {"device_id": "n008_lamp", "command": "On"},
        ]},
        nucore_interface=backend,
    )
    assert result["summary"] == {"total": 2, "successful": 2, "failed": 0}
    assert "error" not in result
    # Exactly one call to send_commands, carrying both entries in order.
    assert len(backend.sent_commands) == 1
    assert [c["device"] for c in backend.sent_commands[0]] == ["n007_udmobile", "n008_lamp"]


@pytest.mark.asyncio
async def test_partial_failure_bad_device_and_bad_command_do_not_block_valid_siblings():
    backend = FakeBackend()
    result = await execute_tool(
        "send_command",
        {"commands": [
            {"device_id": "n008_lamp", "command": "On"},
            {"device_id": "no_such_device", "command": "On"},
            {"device_id": "n007_udmobile", "command": "No Such Command"},
        ]},
        nucore_interface=backend,
    )
    assert result["summary"] == {"total": 3, "successful": 1, "failed": 2}
    assert "error" in result
    assert result["results"][0]["successful"] is True
    assert result["results"][1]["successful"] is False
    assert result["results"][2]["successful"] is False
    # The two resolution failures never reached send_commands.
    assert len(backend.sent_commands) == 1
    assert len(backend.sent_commands[0]) == 1


@pytest.mark.asyncio
async def test_send_commands_exception_marks_only_the_sent_entries_failed():
    backend = FakeBackend()
    backend.raise_on_send = RuntimeError("boom")
    result = await execute_tool(
        "send_command",
        {"commands": [{"device_id": "n008_lamp", "command": "On"}]},
        nucore_interface=backend,
    )
    assert result["summary"] == {"total": 1, "successful": 0, "failed": 1}
    assert result["results"][0]["error"] == "failed to send command: boom"


@pytest.mark.asyncio
async def test_hub_rejects_one_of_two_commands():
    backend = FakeBackend()
    backend.response_status_codes = [200, 500]
    result = await execute_tool(
        "send_command",
        {"commands": [
            {"device_id": "n007_udmobile", "command": "On"},
            {"device_id": "n008_lamp", "command": "On"},
        ]},
        nucore_interface=backend,
    )
    assert result["summary"] == {"total": 2, "successful": 1, "failed": 1}
    assert result["results"][0]["successful"] is True
    assert result["results"][1]["successful"] is False


@pytest.mark.asyncio
async def test_commands_must_be_a_non_empty_list():
    backend = FakeBackend()

    empty = await execute_tool("send_command", {"commands": []}, nucore_interface=backend)
    assert "error" in empty
    assert "results" not in empty

    not_a_list = await execute_tool("send_command", {"commands": "n007_udmobile"}, nucore_interface=backend)
    assert "error" in not_a_list
    assert "results" not in not_a_list

    assert not backend.sent_commands

"""A command with more than one parameter must render each parameter's own
name and value spec separately, in order -- never merge every parameter's
enum labels into one flat list.

Real bug this guards against: UD Mobile's "Send Message"/Notify command
(GV10) has 3 separate parameters -- Group, Sound, Content -- each with its
own enum. The old MinimalRagFormatter._build_command_item combined every
parameter's enum labels into one undifferentiated list under the command
name, so the model saw one 19-option "message type" instead of 3 distinct
parameters to fill -- and had no way to build send_command's positional
`values` array correctly (see tool_device_send_command.json's contract:
"one entry per parameter, in the same order they're listed... in DEVICE
DATABASE").
"""

from __future__ import annotations

from nucore.cmd import Command, CommandParameter
from nucore.editor import Editor, EditorSubsetRange
from nucore.nodedef import NodeCommands, NodeDef
from nucore.uom import UOMEntry
from rag.dedupe_profiles import DedupeProfiles
from rag.minimal_rag_formatter import MinimalRagFormatter

UOM25 = UOMEntry(id="25", description="Enum", label="Enum", name="Enum")


def _enum_editor(editor_id: str, names: dict[str, str]) -> Editor:
    return Editor(
        id=editor_id, is_reference=False,
        ranges=[EditorSubsetRange(id=editor_id, uom=UOM25, subset="0-99", names=names)],
    )


def _udmobile_notify_command() -> Command:
    """Mirrors the real GV10 "Notify"/"Send Message" command exactly:
    3 parameters, each with its own distinct enum."""
    group_editor = _enum_editor("I_GROUP", {"0": "All"})
    sound_editor = _enum_editor(
        "I_SOUND", {"0": "None", "1": "Clock Radio Alarm", "2": "Siren", "3": "Chime"}
    )
    content_editor = _enum_editor("I_CONTENT", {"11": "General Notification"})
    return Command(
        id="GV10",
        name="Send Message",
        parameters=[
            CommandParameter(id="Group", name="Group", editor=group_editor),
            CommandParameter(id="Sound", name="Sound", editor=sound_editor),
            CommandParameter(id="Content", name="Content", editor=content_editor),
        ],
    )


def test_multi_parameter_command_keeps_each_parameter_separate():
    node_def = NodeDef(
        id="UDMobile", properties={},
        cmds=NodeCommands(accepts=[_udmobile_notify_command()], sends=[]),
    )
    result = MinimalRagFormatter(json_output=True)._format_nodedef_json(node_def)
    (item,) = result["accepts-cmds"]

    assert item == {
        "Send Message": [
            ("Group", ["All"]),
            ("Sound", ["None", "Clock Radio Alarm", "Siren", "Chime"]),
            ("Content", ["General Notification"]),
        ]
    }


def test_multi_parameter_command_survives_dedupe_and_python_rendering():
    """End-to-end: the multi-parameter shape must come out the other side
    of DedupeProfiles.render_python as a parseable, order-preserving
    Python literal -- not get flattened, dropped, or mistaken for a
    shareable $ref-able enum."""
    data = {
        "profiles": [
            {
                "id": "UDMobile",
                "accepts-cmds": [
                    {
                        "Send Message": [
                            ("Group", ["All"]),
                            ("Sound", ["None", "Clock Radio Alarm", "Siren", "Chime"]),
                            ("Content", ["General Notification"]),
                        ]
                    }
                ],
                "devices": [{"id": "n007_udmobile", "name": "UD Mobile", "parent": "none"}],
            }
        ],
        "folders": [],
    }
    rendered = DedupeProfiles.render_python(data)

    assert "ENUMS = {" not in rendered  # never mistaken for a $ref-able flat enum
    import ast
    start = rendered.index("PROFILES = {") + len("PROFILES = ")
    end = rendered.index("\nFOLDERS", start) if "\nFOLDERS" in rendered[start:] else len(rendered)
    profiles = ast.literal_eval(rendered[start:end].strip())
    assert profiles["UDMobile"]["accepts"] == [
        (
            "Send Message",
            [
                ("Group", ["All"]),
                ("Sound", ["None", "Clock Radio Alarm", "Siren", "Chime"]),
                ("Content", ["General Notification"]),
            ],
        )
    ]


def test_single_parameter_command_is_unchanged():
    """Regression guard: a plain single-parameter enum command must still
    collapse to the old, simpler (name, values) shape, not the new
    list-of-tuples shape -- that's reserved for genuinely multi-parameter
    commands."""
    editor = _enum_editor("I_MODE", {"0": "Off", "1": "On"})
    command = Command(id="ST", name="Set Mode", parameters=[CommandParameter(id="M", name="Mode", editor=editor)])
    node_def = NodeDef(id="Thermostat", properties={}, cmds=NodeCommands(accepts=[command], sends=[]))

    result = MinimalRagFormatter(json_output=True)._format_nodedef_json(node_def)
    (item,) = result["accepts-cmds"]

    assert item == {"Set Mode": ["Off", "On"]}


def test_parameterless_command_is_unchanged():
    command = Command(id="DOF", name="Off", parameters=[])
    node_def = NodeDef(id="Switch", properties={}, cmds=NodeCommands(accepts=[command], sends=[]))

    result = MinimalRagFormatter(json_output=True)._format_nodedef_json(node_def)
    (item,) = result["accepts-cmds"]

    assert item == {"Off": []}

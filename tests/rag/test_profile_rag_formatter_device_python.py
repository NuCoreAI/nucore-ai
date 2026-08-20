"""ProfileRagFormatter._py_repr_command/_py_repr_property -- get_device_detail's
per-command/per-property rendering. Commands/properties are now referenced by
display name in the routine DSL (routine_automation.py resolves the real id
server-side), so these no longer carry an id at all -- confirms the id is
gone and the name/params/editor info the DSL still needs is intact.
"""

from __future__ import annotations

import ast

from nucore.cmd import Command, CommandParameter
from nucore.editor import Editor, EditorMinMaxRange
from nucore.nodedef import NodeProperty
from nucore.uom import UOMEntry
from rag.profile_rag_formatter import ProfileRagFormatter

UOM_PCT = UOMEntry(id="51", description="Percent", label="%", name="%")


def _pct_editor(editor_id: str = "I_PCT") -> Editor:
    return Editor(id=editor_id, is_reference=False, ranges=[EditorMinMaxRange(id=editor_id, uom=UOM_PCT, min=0, max=100)])


def test_parameterless_command_renders_as_a_bare_name_no_id():
    formatter = ProfileRagFormatter(json_output=True)
    command = Command(id="DOF", name="Off")

    rendered = formatter._py_repr_command(command)

    assert rendered == "'Off'"
    assert "DOF" not in rendered


def test_command_with_params_renders_name_and_params_no_command_id():
    formatter = ProfileRagFormatter(json_output=True)
    command = Command(id="DON", name="On", parameters=[CommandParameter(id="n/a", name=None, editor=_pct_editor())])

    rendered = ast.literal_eval(formatter._py_repr_command(command))

    assert rendered[0] == "On"
    assert "DON" not in formatter._py_repr_command(command)
    # the param itself still carries its own real id -- only the command's
    # own id is dropped
    assert rendered[1][0][1] == "n/a"


def test_property_without_editor_renders_as_a_bare_name_no_id():
    formatter = ProfileRagFormatter(json_output=True)
    prop = NodeProperty(id="ST", editor=None, name="Status")

    rendered = formatter._py_repr_property(prop)

    assert rendered == "'Status'"
    assert "'ST'" not in rendered


def test_property_with_editor_renders_name_and_editor_no_property_id():
    formatter = ProfileRagFormatter(json_output=True)
    prop = NodeProperty(id="ST", editor=_pct_editor("I_PCT"), name="Status")

    rendered = ast.literal_eval(formatter._py_repr_property(prop))

    assert rendered[0] == "Status"
    assert rendered[1] == "I_PCT"  # the editor's own id -- shared-editor cross-reference, not the property's id
    assert "'ST'" not in formatter._py_repr_property(prop)

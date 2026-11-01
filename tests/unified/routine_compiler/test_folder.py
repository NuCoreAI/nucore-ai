import pytest

from unified.routine_compiler import TriggerCompileError, compile_trigger_source


def test_folder_with_condition_compiles_to_isfolder_with_no_then_else():
    compiled = compile_trigger_source(
        name="Vacation Mode",
        trigger_id=None,
        comment=None,
        source='if device("A").was_controlled(command="DON"):\n    pass',
        is_folder=True,
    )
    assert compiled["if"] == [{"type": "control", "andOr": "and", "id": "DON", "node": "A", "op": "IS"}]
    assert compiled["isFolder"] is True
    assert "then" not in compiled and "else" not in compiled


def test_bare_pass_folder_is_always_active_with_no_condition():
    compiled = compile_trigger_source(name="Always On", trigger_id=None, comment=None, source="pass", is_folder=True)
    assert compiled["if"] == []
    assert compiled["isFolder"] is True
    assert "then" not in compiled and "else" not in compiled


def test_folder_with_real_action_is_rejected():
    with pytest.raises(TriggerCompileError, match="do not support actions"):
        compile_trigger_source(
            name="t",
            trigger_id=None,
            comment=None,
            source='if device("A").was_controlled(command="DON"):\n    device("B").command("DON")',
            is_folder=True,
        )


def test_folder_with_else_is_rejected():
    with pytest.raises(TriggerCompileError, match="do not support `else`"):
        compile_trigger_source(
            name="t",
            trigger_id=None,
            comment=None,
            source='if device("A").was_controlled(command="DON"):\n    pass\nelse:\n    pass',
            is_folder=True,
        )


def test_folder_with_elif_still_rejected():
    with pytest.raises(TriggerCompileError, match="`elif` is not supported"):
        compile_trigger_source(
            name="t",
            trigger_id=None,
            comment=None,
            source=(
                'if device("A").was_controlled(command="DON"):\n'
                "    pass\n"
                'elif device("B").was_controlled(command="DON"):\n'
                "    pass"
            ),
            is_folder=True,
        )


def test_folder_with_multiple_top_level_statements_is_rejected():
    with pytest.raises(TriggerCompileError, match="exactly one statement"):
        compile_trigger_source(
            name="t",
            trigger_id=None,
            comment=None,
            source='pass\ndevice("A").command("DON")',
            is_folder=True,
        )


def test_folder_with_action_only_shape_is_rejected():
    """The ordinary condition-less "actions only" routine shape is
    nonsensical for a folder -- must not silently be accepted as one."""
    with pytest.raises(TriggerCompileError, match="exactly one statement"):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source='device("A").command("DON")', is_folder=True)


def test_folder_passthrough_of_parent_comment_and_trigger_id():
    compiled = compile_trigger_source(
        name="Vacation Mode",
        trigger_id=42,
        comment="a folder",
        source="pass",
        parent=7,
        is_folder=True,
    )
    assert compiled["id"] == 42
    assert compiled["parent"] == 7
    assert compiled["comment"] == "a folder"


def test_is_folder_defaults_to_false_and_does_not_change_ordinary_compilation():
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source='device("A").command("DON")')
    assert "isFolder" not in compiled
    assert compiled["then"] == [{"type": "cmd", "id": "DON", "node": "A", "p": []}]
    assert compiled["else"] == []

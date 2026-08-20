import pytest

from unified.routine_compiler import TriggerCompileError, compile_trigger_source


def test_routine_is_true():
    compiled = compile_trigger_source(
        name="t", trigger_id=None, comment=None, source='if routine_is_true(5):\n    device("A").command("DON")'
    )
    assert compiled["if"] == [{"type": "triggerref", "refid": 5, "is": True, "andOr": "and"}]


def test_routine_is_false():
    compiled = compile_trigger_source(
        name="t", trigger_id=None, comment=None, source='if routine_is_false(5):\n    device("A").command("DON")'
    )
    assert compiled["if"][0]["is"] is False


@pytest.mark.parametrize(
    "dsl_name,schema_type",
    [
        ("run_if", "runif"),
        ("run_then", "runthen"),
        ("run_else", "runelse"),
        ("enable_routine", "enable"),
        ("disable_routine", "disable"),
        ("stop_routine", "stop"),
        ("enable_run_at_startup", "rebootrun"),
        ("disable_run_at_startup", "rebootnotrun"),
    ],
)
def test_program_control_actions(dsl_name, schema_type):
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source=f"{dsl_name}(7)")
    assert compiled["then"] == [{"type": schema_type, "id": 7}]


def test_program_control_requires_routine_id():
    with pytest.raises(TriggerCompileError, match="requires a routine id"):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source="run_if()")

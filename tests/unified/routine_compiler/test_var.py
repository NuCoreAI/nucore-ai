import pytest

from unified.routine_compiler import TriggerCompileError, compile_trigger_source


def test_var_condition_literal():
    compiled = compile_trigger_source(
        name="t", trigger_id=None, comment=None, source='if var_ref(id=5, type=1) > 10:\n    device("A").command("DON")'
    )
    assert compiled["if"] == [
        {"type": "var", "id": 5, "varType": "1", "op": "GT", "val": {"value": 10}, "andOr": "and"}
    ]


def test_var_condition_vs_another_var():
    compiled = compile_trigger_source(
        name="t",
        trigger_id=None,
        comment=None,
        source='if var_ref(id=5, type=1) == var_ref(id=6, type=2):\n    device("A").command("DON")',
    )
    assert compiled["if"][0]["var"] == {"id": 6, "type": "2"}
    assert "val" not in compiled["if"][0]


def test_set_var_literal():
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source='set_var(id=1, type=1, op="=", value=42)')
    assert compiled["then"] == [{"type": "var", "varType": "1", "id": 1, "op": "EQ", "val": {"value": 42}}]


def test_set_var_from_another_var():
    compiled = compile_trigger_source(
        name="t", trigger_id=None, comment=None, source='set_var(id=2, type=1, op="+=", var=var_ref(id=1, type=1))'
    )
    assert compiled["then"] == [{"type": "var", "varType": "1", "id": 2, "op": "ADD=", "var": {"id": 1, "type": "1"}}]


def test_set_var_from_device_status():
    compiled = compile_trigger_source(
        name="t",
        trigger_id=None,
        comment=None,
        source='set_var(id=3, type=2, op="=", device="A", property="ST", uom=17)',
    )
    assert compiled["then"] == [
        {"type": "var", "varType": "2", "id": 3, "op": "EQ", "status": {"id": "ST", "node": "A", "uom": 17}}
    ]


def test_set_var_from_sysval():
    compiled = compile_trigger_source(
        name="t", trigger_id=None, comment=None, source='set_var(id=4, type=1, op="=", sysval="CurrentHour")'
    )
    assert compiled["then"] == [{"type": "var", "varType": "1", "id": 4, "op": "EQ", "sysval": {"id": 8}}]


def test_set_var_requires_exactly_one_source():
    with pytest.raises(TriggerCompileError, match="exactly one of"):
        compile_trigger_source(
            name="t",
            trigger_id=None,
            comment=None,
            source='set_var(id=1, type=1, op="=", value=1, var=var_ref(id=2, type=1))',
        )


def test_while_repeat():
    compiled = compile_trigger_source(
        name="t",
        trigger_id=None,
        comment=None,
        source='device("A").command("DON")\nwhile_repeat(id=1, type=1, op=">", value=0)\ndevice("A").command("DOF")',
    )
    assert compiled["then"][1] == {
        "type": "repeat",
        "while": {"var": {"op": "GT", "varType": "1", "id": 1, "val": {"value": 0}}},
    }

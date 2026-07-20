import pytest

from unified.routine_compiler import TriggerCompileError, compile_trigger_source


def test_var_condition_literal():
    compiled = compile_trigger_source(
        name="t",
        trigger_id=None,
        comment=None,
        source='if var_ref(id=5, type=1, precision=0) > 10:\n    device("A").command("DON")',
    )
    assert compiled["if"] == [
        {"type": "var", "id": 5, "varType": "1", "op": "GT", "val": {"value": 10, "prec": 0}, "andOr": "and"}
    ]


def test_var_condition_literal_scales_by_precision():
    """Confirmed: variable values are precision-scaled integers on the wire,
    same raw*10**prec convention as device command params -- the compiler
    does this math, not the model."""
    compiled = compile_trigger_source(
        name="t",
        trigger_id=None,
        comment=None,
        source='if var_ref(id=5, type=1, precision=1) > 4.2:\n    device("A").command("DON")',
    )
    assert compiled["if"][0]["val"] == {"value": 42, "prec": 1}


def test_var_condition_vs_another_var():
    compiled = compile_trigger_source(
        name="t",
        trigger_id=None,
        comment=None,
        source='if var_ref(id=5, type=1, precision=0) == var_ref(id=6, type=2, precision=0):\n    device("A").command("DON")',
    )
    assert compiled["if"][0]["var"] == {"id": 6, "type": "2"}
    assert "val" not in compiled["if"][0]


def test_var_ref_requires_precision():
    with pytest.raises(TriggerCompileError, match="precision"):
        compile_trigger_source(
            name="t",
            trigger_id=None,
            comment=None,
            source='if var_ref(id=5, type=1) > 10:\n    device("A").command("DON")',
        )


def test_set_var_literal():
    compiled = compile_trigger_source(
        name="t", trigger_id=None, comment=None, source='set_var(id=1, type=1, op="=", value=42, precision=0)'
    )
    assert compiled["then"] == [{"type": "var", "varType": "1", "id": 1, "op": "EQ", "val": {"value": 42, "prec": 0}}]


def test_set_var_literal_scales_by_precision():
    compiled = compile_trigger_source(
        name="t", trigger_id=None, comment=None, source='set_var(id=1, type=1, op="=", value=4.2, precision=1)'
    )
    assert compiled["then"][0]["val"] == {"value": 42, "prec": 1}


def test_set_var_value_requires_precision():
    with pytest.raises(TriggerCompileError, match="precision"):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source='set_var(id=1, type=1, op="=", value=42)')


def test_set_var_from_another_var():
    compiled = compile_trigger_source(
        name="t",
        trigger_id=None,
        comment=None,
        source='set_var(id=2, type=1, op="+=", var=var_ref(id=1, type=1, precision=0))',
    )
    assert compiled["then"] == [{"type": "var", "varType": "1", "id": 2, "op": "ADD=", "var": {"id": 1, "type": "1"}}]


def test_set_var_var_mode_rejects_precision():
    with pytest.raises(TriggerCompileError, match="doesn't take precision"):
        compile_trigger_source(
            name="t",
            trigger_id=None,
            comment=None,
            source='set_var(id=2, type=1, op="+=", var=var_ref(id=1, type=1, precision=0), precision=0)',
        )


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
            source='set_var(id=1, type=1, op="=", value=1, var=var_ref(id=2, type=1, precision=0))',
        )


def test_set_var_op_init_takes_no_source():
    """Confirmed: op="init" restores the variable from its stored init
    value -- a bare statement, no value=/var=/device=/sysval= needed."""
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source='set_var(id=1, type=1, op="init")')
    assert compiled["then"] == [{"type": "var", "varType": "1", "id": 1, "op": "INIT"}]


def test_set_var_op_init_rejects_a_source():
    with pytest.raises(TriggerCompileError, match="takes no source"):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source='set_var(id=1, type=1, op="init", value=1)')


def test_while_repeat():
    compiled = compile_trigger_source(
        name="t",
        trigger_id=None,
        comment=None,
        source=(
            'device("A").command("DON")\n'
            'while_repeat(id=1, type=1, op=">", value=0, precision=0)\n'
            'device("A").command("DOF")'
        ),
    )
    assert compiled["then"][1] == {
        "type": "repeat",
        "while": {"var": {"op": "GT", "varType": "1", "id": 1, "val": {"value": 0, "prec": 0}}},
    }


def test_while_repeat_scales_by_precision():
    compiled = compile_trigger_source(
        name="t",
        trigger_id=None,
        comment=None,
        source='while_repeat(id=1, type=1, op=">", value=4.2, precision=1)\ndevice("A").command("DON")',
    )
    assert compiled["then"][0]["while"]["var"]["val"] == {"value": 42, "prec": 1}


def test_while_repeat_requires_precision():
    with pytest.raises(TriggerCompileError, match="missing required argument"):
        compile_trigger_source(
            name="t",
            trigger_id=None,
            comment=None,
            source='while_repeat(id=1, type=1, op=">", value=0)\ndevice("A").command("DON")',
        )

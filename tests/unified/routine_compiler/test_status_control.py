import pytest

from unified.routine_compiler import TriggerCompileError, compile_trigger_source


def test_status_condition_scales_by_precision():
    code = 'if device("A").status("ST", uom=17, precision=1) > 75.5:\n    device("B").command("DON")'
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)
    assert compiled["if"] == [
        {"type": "status", "id": "ST", "node": "A", "op": "GT", "val": {"value": 755, "prec": 1, "uom": 17}, "andOr": "and"}
    ]


def test_status_condition_uom_written_as_a_string_still_compiles_to_an_int():
    # get_device_detail's own editor rendering shows uom as a string -- a
    # model copying that literally into status(uom="17", ...) must still
    # produce a schema-valid int, per trigger-new.json's val.uom: number.
    code = 'if device("A").status("ST", uom="17", precision=1) > 75.5:\n    device("B").command("DON")'
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)
    uom = compiled["if"][0]["val"]["uom"]
    assert uom == 17
    assert isinstance(uom, int)


def test_status_condition_index_uom_never_scaled():
    code = 'if device("A").status("ST", uom=25, precision=0) == 3:\n    device("B").command("DON")'
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)
    assert compiled["if"][0]["val"] == {"value": 3, "prec": 0, "uom": 25}
    assert compiled["if"][0]["op"] == "IS"


@pytest.mark.parametrize(
    "op,expected",
    [(">", "GT"), (">=", "GE"), ("<", "LT"), ("<=", "LE"), ("==", "IS"), ("!=", "ISNOT")],
)
def test_status_condition_operator_mapping(op, expected):
    code = f'if device("A").status("ST", uom=17, precision=0) {op} 5:\n    device("B").command("DON")'
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)
    assert compiled["if"][0]["op"] == expected


def test_status_condition_requires_uom_and_precision():
    with pytest.raises(TriggerCompileError, match="uom and precision"):
        compile_trigger_source(
            name="t", trigger_id=None, comment=None, source='if device("A").status("ST") > 5:\n    pass'
        )


def test_control_condition_default_eq_is():
    code = 'if device("A").was_controlled(command="DON"):\n    device("B").command("DOF")'
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)
    assert compiled["if"] == [{"type": "control", "id": "DON", "node": "A", "op": "IS", "andOr": "and"}]


def test_control_condition_eq_isnot():
    code = 'if device("A").was_controlled(command="DON", eq="isnot"):\n    device("B").command("DOF")'
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)
    assert compiled["if"][0]["op"] == "ISNOT"


def test_control_condition_rejects_params():
    """The new schema's Control type has no `parameters` field at all --
    a real behavior reduction vs. v1, not a bug."""
    code = (
        'if device("A").was_controlled(command="DON", params=[param(id="OL", value=1, uom=51, precision=0)]):\n'
        "    pass"
    )
    with pytest.raises(TriggerCompileError, match="does not accept params"):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)

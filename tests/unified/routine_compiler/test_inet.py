import pytest

from unified.routine_compiler import TriggerCompileError, compile_trigger_source


def _if(code: str):
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)
    return compiled["if"][0]


def test_utility_price():
    cond = _if('if utility_price(op=">", value=0.25):\n    device("A").command("DOF")')
    assert cond["type"] == "inet" and cond["id"] == "oadr"
    assert cond["control"] == "price" and cond["op"] == "GT" and cond["action"] == 0.25


def test_utility_status():
    cond = _if('if utility_status(op="is", value="active"):\n    device("A").command("DOF")')
    assert cond["control"] == "status" and cond["op"] == "IS" and cond["action"] == "active"


def test_utility_mode():
    cond = _if('if utility_mode(op="isnot", value="high"):\n    device("A").command("DOF")')
    assert cond["control"] == "mode" and cond["op"] == "ISNOT" and cond["action"] == "high"


def test_utility_status_rejects_unknown_value():
    with pytest.raises(TriggerCompileError, match="must be one of"):
        compile_trigger_source(
            name="t",
            trigger_id=None,
            comment=None,
            source='if utility_status(op="is", value="bogus"):\n    pass',
        )

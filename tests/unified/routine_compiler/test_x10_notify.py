import pytest

from unified.routine_compiler import TriggerCompileError, compile_trigger_source


def test_x10_condition_and_action():
    compiled = compile_trigger_source(
        name="t",
        trigger_id=None,
        comment=None,
        source='if x10_event(house="B", unit=3, command=2, eq="is"):\n    x10_send(house="B", command=2)',
    )
    assert compiled["if"] == [{"type": "x10", "hc": "B", "cc": 2, "uc": 3, "op": "IS", "andOr": "and"}]
    assert compiled["then"] == [{"type": "x10", "hc": "B", "cc": 2}]


def test_x10_house_must_be_a_p():
    with pytest.raises(TriggerCompileError, match="A-P"):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source='x10_send(house="Z", command=1)')


def test_x10_command_range():
    with pytest.raises(TriggerCompileError, match="0-15"):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source='x10_send(house="A", command=99)')


def test_notify_minimal():
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source="notify(recipient=42)")
    assert compiled["then"] == [{"type": "notify", "recipient": 42}]


def test_notify_with_content():
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source="notify(recipient=42, content=7)")
    assert compiled["then"] == [{"type": "notify", "recipient": 42, "content": 7}]


def test_notify_requires_recipient():
    with pytest.raises(TriggerCompileError, match="requires recipient"):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source="notify()")

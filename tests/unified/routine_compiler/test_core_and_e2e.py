import pytest

from unified.routine_compiler import TriggerCompileError, compile_trigger_source


def test_bare_string_conditions_and_actions_are_silently_dropped():
    """Hard rule: a routine has exactly ONE comment -- its own `comment`
    field, supplied separately, never authored inline in the DSL. A bare
    string anywhere in if/then/else is not a valid construct there and is
    stripped rather than compiled into a node."""
    compiled = compile_trigger_source(
        name="t",
        trigger_id=None,
        comment=None,
        source='if "just a note":\n    "did the thing"\n    device("A").command("DON")',
    )
    assert compiled["if"] == []
    assert compiled["then"] == [{"type": "cmd", "id": "DON", "node": "A", "p": []}]


def test_bare_string_condition_mixed_with_a_real_condition_is_dropped_not_compiled():
    compiled = compile_trigger_source(
        name="t",
        trigger_id=None,
        comment=None,
        source='if "note" and device("A").was_controlled(command="DON"):\n    device("B").command("DON")',
    )
    assert compiled["if"] == [{"type": "control", "andOr": "and", "id": "DON", "node": "A", "op": "IS"}]


def test_bare_string_action_in_else_block_is_dropped():
    compiled = compile_trigger_source(
        name="t",
        trigger_id=None,
        comment=None,
        source=(
            'if device("A").was_controlled(command="DON"):\n'
            '    device("B").command("DON")\n'
            "else:\n"
            '    "turning it off instead"\n'
            '    device("B").command("DOF")'
        ),
    )
    assert compiled["else"] == [{"type": "cmd", "id": "DOF", "node": "B", "p": []}]


def test_paren_generalizes_mixed_and_or_without_explicit_parens():
    """Python's own operator precedence resolves `a and b or c` into nested
    BoolOp nodes identical to `(a and b) or c` -- parenthesization doesn't
    survive into the ast, only the grouping it implies does. Confirms the
    compiler doesn't need separate "did the user write parens" tracking."""
    code = (
        'if device("A").was_controlled(command="DON") and device("B").was_controlled(command="DON") '
        'or device("C").was_controlled(command="DOF"):\n'
        '    device("D").command("DON")'
    )
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)
    ifs = compiled["if"]
    assert len(ifs) == 2
    assert ifs[0]["type"] == "paren" and ifs[0]["andOr"] == "or"
    assert [c["node"] for c in ifs[0]["conditions"]] == ["A", "B"]
    assert all(c["andOr"] == "and" for c in ifs[0]["conditions"])
    assert ifs[1]["node"] == "C" and ifs[1]["andOr"] == "or"


def test_condition_less_action_only_routine():
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source='device("A").command("DON")')
    assert compiled["if"] == []
    assert compiled["then"] == [{"type": "cmd", "id": "DON", "node": "A", "p": []}]
    assert compiled["else"] == []


def test_elif_rejected():
    code = (
        'if device("A").was_controlled(command="DON"):\n'
        '    device("B").command("DON")\n'
        "elif device(\"C\").was_controlled(command=\"DON\"):\n"
        '    device("B").command("DOF")'
    )
    with pytest.raises(TriggerCompileError, match="elif"):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)


def test_create_omits_id_update_includes_it():
    create = compile_trigger_source(name="t", trigger_id=None, comment=None, source='device("A").command("DON")')
    assert "id" not in create

    update = compile_trigger_source(name="t", trigger_id=7, comment=None, source='device("A").command("DON")')
    assert update["id"] == 7


def test_parent_omitted_by_default_but_included_when_given():
    """parent is routine-placement metadata, never expressed in the DSL --
    confirmed the real update endpoint requires it, so the compiler must
    pass through whatever the caller supplies without altering it."""
    no_parent = compile_trigger_source(name="t", trigger_id=None, comment=None, source='device("A").command("DON")')
    assert "parent" not in no_parent

    with_parent = compile_trigger_source(
        name="t", trigger_id=7, comment=None, source='device("A").command("DON")', parent=5
    )
    assert with_parent["parent"] == 5


def test_empty_code_rejected():
    with pytest.raises(TriggerCompileError, match="empty"):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source="")


def test_full_multi_construct_routine_compiles_to_expected_shape():
    """One representative routine touching a broad cross-section of
    construct families in a single compile, as an end-to-end smoke test."""
    code = (
        'if device("25 80 3C 1").status("ST", uom=17, precision=1) > 75.5 and '
        '(device("KP1").was_controlled(command="DON", eq="is") or device("KP2").was_controlled(command="DON")):\n'
        '    device("BAR1").command("DON", params=[param(id="OL", value=100, uom=51, precision=0)])\n'
        "    wait(duration(minute=10))\n"
        "    repeat(count=2)\n"
        '    device("BAR1").command("DOF")\n'
        "else:\n"
        '    device("BAR1").command("DOF")'
    )
    compiled = compile_trigger_source(name="Full Test", trigger_id=7, comment="a test", source=code)

    assert compiled["name"] == "Full Test"
    assert compiled["id"] == 7
    assert compiled["comment"] == "a test"

    assert compiled["if"][0]["type"] == "status" and compiled["if"][0]["val"] == {"value": 755, "prec": 1, "uom": 17}
    assert compiled["if"][1]["type"] == "paren"
    assert len(compiled["if"][1]["conditions"]) == 2

    assert compiled["then"][0]["type"] == "cmd" and compiled["then"][0]["p"][0]["id"] == "OL"
    assert compiled["then"][1] == {"type": "wait", "minutes": 10}
    assert compiled["then"][2] == {"type": "repeat", "for": {"times": 2}}
    assert compiled["then"][3]["type"] == "cmd" and compiled["then"][3]["id"] == "DOF"

    assert compiled["else"][0]["type"] == "cmd" and compiled["else"][0]["id"] == "DOF"

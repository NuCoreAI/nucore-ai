import pytest

from unified.routine_compiler import TriggerCompileError, compile_trigger_source


def _then(code: str):
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)
    return compiled["then"]


def test_command_no_params():
    assert _then('device("A").command("DON")') == [{"type": "cmd", "id": "DON", "node": "A", "p": []}]


def test_command_with_params():
    then = _then('device("A").command("DON", params=[param(id="OL", value=100, uom=51, precision=0)])')
    assert then[0]["p"] == [{"type": "val", "id": "OL", "val": {"value": 100, "prec": 0, "uom": 51}}]


def test_command_param_with_no_id_compiles_to_empty_string_id():
    # An anonymous parameter (no id= given at all) used to fall back to the
    # sentinel "n/a" -- now it's an empty string instead, matching the tool
    # description's documented convention (id="").
    then = _then('device("A").command("DON", params=[param(value=100, uom=51, precision=0)])')
    assert then[0]["p"] == [{"type": "val", "id": "", "val": {"value": 100, "prec": 0, "uom": 51}}]


def test_command_param_legacy_na_id_normalizes_to_empty_string():
    # Backward compatibility: if id="n/a" is still written (the old
    # documented convention), it normalizes to "" too, not passed through
    # literally.
    then = _then('device("A").command("DON", params=[param(id="n/a", value=100, uom=51, precision=0)])')
    assert then[0]["p"] == [{"type": "val", "id": "", "val": {"value": 100, "prec": 0, "uom": 51}}]


def test_command_param_uom_written_as_a_string_still_compiles_to_an_int():
    # get_device_detail's own editor rendering shows uom as a string
    # (Editor/EditorMinMaxRange.to_dict()) -- a model copying that literally
    # into param(uom="51", ...) must still produce a schema-valid int, per
    # trigger-new.json's ValWithUom.uom: number.
    then = _then('device("A").command("DON", params=[param(id="OL", value=100, uom="51", precision=0)])')
    param = then[0]["p"][0]
    assert param["val"]["uom"] == 51
    assert isinstance(param["val"]["uom"], int)


def test_wait_duration_decomposes_to_named_fields():
    assert _then("wait(duration(minute=10))") == [{"type": "wait", "minutes": 10}]
    assert _then("wait(duration(hour=1, minute=2, second=3))") == [
        {"type": "wait", "hours": 1, "minutes": 2, "seconds": 3}
    ]


def test_wait_random():
    then = _then("wait(duration(minute=5), random=True)")
    assert then[0]["random"] is True


def test_repeat_is_a_flat_marker_not_a_wrapping_block():
    """Confirmed schema fact: Repeat has no body field -- everything
    textually after the marker, in the same then/else list, repeats."""
    then = _then(
        'device("A").command("DON")\n'
        "repeat(count=3)\n"
        'device("A").command("DOF")\n'
        "wait(duration(second=1))"
    )
    assert then[0]["type"] == "cmd" and then[0]["id"] == "DON"
    assert then[1] == {"type": "repeat", "for": {"times": 3}}
    assert then[2]["type"] == "cmd" and then[2]["id"] == "DOF"
    assert then[3] == {"type": "wait", "seconds": 1}


def test_repeat_random():
    # a trailing action is required -- a repeat marker with nothing after it
    # repeats zero actions and is rejected (see test_repeat_marker_at_end_of_block_is_rejected)
    then = _then('repeat(count=2, random=True)\ndevice("A").command("DON")')
    assert then[0] == {"type": "repeat", "for": {"times": 2, "random": True}}


def test_every():
    then = _then('every(duration(hour=2))\ndevice("A").command("DON")')
    assert then[0] == {"type": "repeat", "every": {"hours": 2}}


def test_repeat_requires_count():
    with pytest.raises(TriggerCompileError, match="requires count"):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source="repeat()")


def test_python_for_loop_syntax_is_rejected():
    """v1's `for _ in repeat(...): <body>` syntax is no longer valid --
    the new schema has nowhere to put a nested body."""
    with pytest.raises(TriggerCompileError):
        compile_trigger_source(
            name="t",
            trigger_id=None,
            comment=None,
            source='for _ in repeat(count=3):\n    device("A").command("DON")',
        )


def test_repeat_marker_at_end_of_block_is_rejected():
    """Real, observed model mistake: writing repeat(...) AFTER the actions
    it was meant to repeat (natural phrasing narrates "repeat 5 times"
    last, but the DSL requires the opposite order). A marker with nothing
    after it repeats zero actions by definition -- always wrong, not just
    unusual -- so the compiler rejects it instead of silently compiling a
    no-op repeat."""
    code = (
        'if weekly_at(days="mon,tue", time="05:00:00"):\n'
        '    device("17 F8 44 1").command("DON")\n'
        "    wait(duration(minute=1))\n"
        '    device("17 F8 44 1").command("DOF")\n'
        "    repeat(count=5)"
    )
    with pytest.raises(TriggerCompileError, match="must come BEFORE"):
        compile_trigger_source(name="t", trigger_id=15, comment=None, source=code)


def test_repeat_marker_before_actions_compiles_correctly():
    code = (
        'if weekly_at(days="mon,tue", time="05:00:00"):\n'
        "    repeat(count=5)\n"
        '    device("17 F8 44 1").command("DON")\n'
        "    wait(duration(minute=1))\n"
        '    device("17 F8 44 1").command("DOF")'
    )
    compiled = compile_trigger_source(name="t", trigger_id=15, comment=None, source=code)
    assert compiled["then"][0] == {"type": "repeat", "for": {"times": 5}}
    assert [a["type"] for a in compiled["then"][1:]] == ["cmd", "wait", "cmd"]


def test_repeat_marker_at_end_of_else_block_is_also_rejected():
    code = (
        'if device("A").was_controlled(command="DON"):\n'
        '    device("B").command("DON")\n'
        "else:\n"
        '    device("B").command("DOF")\n'
        "    repeat(count=2)"
    )
    with pytest.raises(TriggerCompileError, match="must come BEFORE"):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)

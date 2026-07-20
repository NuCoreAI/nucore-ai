import pytest

from unified.routine_compiler import TriggerCompileError, compile_trigger_source


def test_adjust_scene_with_command():
    code = (
        'adjust_scene(group="SCENE1", controller="SCENE1", node="B", type="cmd", '
        'command="DFON", params=[param(id="n/a", value=50, uom=51, precision=0)])'
    )
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)
    assert compiled["then"] == [
        {
            "type": "lp",
            "group": "SCENE1",
            "ctlId": "SCENE1",
            "rsp": {
                "type": "cmd",
                "node": "B",
                "cmd": {"cmdId": "DFON", "p": [{"type": "val", "id": "n/a", "val": {"value": 50, "prec": 0, "uom": 51}}]},
            },
        }
    ]


def test_adjust_scene_default_no_command():
    code = 'adjust_scene(group="S", controller="S", node="B", type="default")'
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)
    assert compiled["then"][0]["rsp"] == {"type": "default", "node": "B"}


def test_adjust_scene_params_without_command_rejected():
    code = 'adjust_scene(group="S", controller="S", node="B", type="cmd", params=[param(id="n/a", value=1, uom=51, precision=0)])'
    with pytest.raises(TriggerCompileError, match="requires command"):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)


def test_adjust_scene_invalid_type_rejected():
    code = 'adjust_scene(group="S", controller="S", node="B", type="native")'
    with pytest.raises(TriggerCompileError, match="type="):
        compile_trigger_source(name="t", trigger_id=None, comment=None, source=code)


def test_restart_hub():
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source="restart_hub()")
    assert compiled["then"] == [{"type": "sys", "cmd": 1}]


def test_demand_price_alert():
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source="demand_price_alert()")
    assert compiled["then"] == [{"type": "sys", "cmd": 17}]


def test_query_all_default_control():
    compiled = compile_trigger_source(name="t", trigger_id=None, comment=None, source='query_all(group="G1")')
    assert compiled["then"] == [{"type": "device", "group": "G1", "control": "ST"}]


def test_query_all_explicit_property():
    compiled = compile_trigger_source(
        name="t", trigger_id=None, comment=None, source='query_all(group="G1", property="CLIMD")'
    )
    assert compiled["then"][0]["control"] == "CLIMD"

import ast

from rag.dedupe_routines import DedupeRoutines


def _routines_literal(rendered: str):
    marker = "ROUTINES = "
    start = rendered.index(marker) + len(marker)
    return ast.literal_eval(rendered[start:])


def test_render_python_includes_variable_names_and_runtime_summary_fields():
    routines = [
        {
            "id": 42, "name": "Bedtime", "comment": "test", "device_names": ["Left Hallway"],
            "variable_names": ["Irrigation_Mode"],
            "folder": False, "enabled": True, "running": False, "status": True,
            "runAtStartup": False, "lastRunTime": "2026-07-19T05:00:00",
            "lastFinishTime": "2026-07-19T05:00:01", "nextScheduledRunTime": "2026-07-20T05:00:00",
        },
    ]
    rendered = DedupeRoutines.render_python(routines)
    parsed = _routines_literal(rendered)

    assert parsed == [
        (42, "Bedtime", "test", ["Left Hallway"], ["Irrigation_Mode"], False, None,
         False, True, False, True, False,
         "2026-07-19T05:00:00", "2026-07-19T05:00:01", "2026-07-20T05:00:00"),
    ]


def test_render_python_defaults_missing_variable_names_and_runtime_fields():
    routines = [{"id": 1, "name": "No Runtime Data", "comment": "", "device_names": []}]
    rendered = DedupeRoutines.render_python(routines)
    parsed = _routines_literal(rendered)

    assert parsed == [
        (1, "No Runtime Data", "", [], [], False, None, None, None, None, None, None, None, None, None)
    ]

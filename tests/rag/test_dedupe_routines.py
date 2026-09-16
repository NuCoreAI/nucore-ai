import ast

from rag.dedupe_routines import DedupeRoutines


def _routines_literal(rendered: str):
    marker = "ROUTINES = "
    start = rendered.index(marker) + len(marker)
    return ast.literal_eval(rendered[start:])


def test_render_python_includes_variable_names_and_runtime_summary_fields():
    """Only the structural runtime fields (folder/enabled/run_at_startup) are
    rendered into the always-inlined summary -- live state (running/status/
    last-run-time/etc.) is deliberately excluded and must come from
    get_routine_details's running_state instead (see dedupe_routines.py's
    module docstring / PYTHON_LEGEND)."""
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
         False, True, False),
    ]
    data_section = rendered[rendered.index("ROUTINES = ["):]
    assert "lastRunTime" not in data_section and "2026-07-19" not in data_section


def test_render_python_defaults_missing_variable_names_and_runtime_fields():
    routines = [{"id": 1, "name": "No Runtime Data", "comment": "", "device_names": []}]
    rendered = DedupeRoutines.render_python(routines)
    parsed = _routines_literal(rendered)

    assert parsed == [
        (1, "No Runtime Data", "", [], [], False, None, None, None, None)
    ]

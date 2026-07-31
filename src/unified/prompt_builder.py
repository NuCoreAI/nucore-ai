"""Assembles the single unified system prompt.

Reads ``nucore_interface``/``rag`` directly to build the compact
``DEVICE DATABASE``/``ROUTINES DATABASE`` sections -- no config-file/
directory-loading machinery involved.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nucore import NuCoreInterface
from rag import DedupeRoutines

_PROMPT_DIR = Path(__file__).parent / "prompt"

# (time_data key, rendered Python variable name), in display order.
_TIME_INFO_VARS = (
    ("current_time", "CURRENT_TIME"),
    ("timezone", "TIMEZONE"),
    ("latitude", "LATITUDE"),
    ("longitude", "LONGITUDE"),
    ("sunrise", "SUNRISE_TODAY"),
    ("sunset", "SUNSET_TODAY"),
)


def _render_time_info(time_data: dict[str, Any] | None) -> str:
    """Render ``get_timespecs()``'s result as Python literals -- same
    rendering convention as DEVICE DATABASE/ROUTINES DATABASE below it."""
    if not time_data:
        return "```python\n# Time/timezone/location information is unavailable.\n```"
    lines = [
        f"{var_name} = {time_data[key]!r}" for key, var_name in _TIME_INFO_VARS if time_data.get(key) is not None
    ]
    return f"```python\n{chr(10).join(lines)}\n```"


async def build_system_prompt(nucore_interface: NuCoreInterface) -> str:
    """Build the complete unified system prompt for the given backend."""
    await nucore_interface._refresh_routines_database()

    system_prompt = (_PROMPT_DIR / "system_prompt.md").read_text(encoding="utf-8").strip()
    definitions = (_PROMPT_DIR / "definitions.md").read_text(encoding="utf-8").strip()
    ui_navigation_rules = (_PROMPT_DIR / "ui_navigation_rules.md").read_text(encoding="utf-8").strip()

    device_database = (
        nucore_interface.summary_rags.docs_to_string() if nucore_interface.summary_rags else ""
    )
    routines_database = f"```python\n{DedupeRoutines.render_python(nucore_interface.condensed_routines)}\n```"

    try:
        time_data = await nucore_interface.get_timespecs()
    except NotImplementedError:
        time_data = None
    time_info = _render_time_info(time_data)

    prompt = system_prompt.replace("<<definitions>>", definitions)
    prompt = prompt.replace("<<ui_navigation_rules>>", ui_navigation_rules)
    prompt = prompt.replace("<<device_database>>", device_database)
    prompt = prompt.replace("<<routines_database>>", routines_database)
    prompt = prompt.replace("<<time_info>>", time_info)
    return prompt

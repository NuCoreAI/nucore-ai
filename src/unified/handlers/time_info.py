"""``get_time_info`` -- the exact current time, today's sunrise/sunset, and
this installation's timezone/lat/long, for when TIME & LOCATION's standing
CURRENT_DATE/TIMEZONE/LATITUDE/LONGITUDE (see prompt_builder.py) isn't
precise enough. Thin pass-through: NuCoreInterface.get_timespecs() already
does the real work (and, for IoXWrapper, the caching -- see its own
docstring), this just renames its snake_case keys to the same display names
TIME & LOCATION already uses in the prompt, one name per concept everywhere
the model sees it, prompt or tool result alike.
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface

# (get_timespecs() key, display name), same convention/names as
# prompt_builder.py's _TIME_INFO_VARS -- kept separate since the two render
# different subsets (the prompt: date-only and no sunrise/sunset; this tool:
# everything, full precision).
_TIME_SPECS_VARS = (
    ("current_time", "CURRENT_TIME"),
    ("timezone", "TIMEZONE"),
    ("latitude", "LATITUDE"),
    ("longitude", "LONGITUDE"),
    ("sunrise", "SUNRISE_TODAY"),
    ("sunset", "SUNSET_TODAY"),
)


def render_time_specs(time_data: dict[str, Any]) -> dict[str, Any]:
    """Rename get_timespecs()'s snake_case keys to TIME & LOCATION's display
    names. Values are passed through unchanged -- already ISO-8601 with
    offset for current_time/sunrise/sunset (see IoXWrapper.get_timespecs),
    never raw Unix epoch."""
    return {display_name: time_data[key] for key, display_name in _TIME_SPECS_VARS if time_data.get(key) is not None}


async def get_time_info(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    try:
        time_data = await nucore_interface.get_timespecs()
    except NotImplementedError:
        time_data = None
    if not isinstance(time_data, dict):
        return {"error": "time/timezone/location information is unavailable"}
    return render_time_specs(time_data)

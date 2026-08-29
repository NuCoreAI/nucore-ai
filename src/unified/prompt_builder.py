"""Assembles the single unified system prompt.

Reads ``nucore_interface``/``rag`` directly to build the compact
``DEVICE DATABASE``/``ROUTINES DATABASE`` sections -- no config-file/
directory-loading machinery involved.
"""

from __future__ import annotations

import platform
from pathlib import Path
from typing import Any

from nucore import NuCoreInterface
from rag import DedupeRoutines

from .preferences.preference_store import get_store

_PROMPT_DIR = Path(__file__).parent / "prompt"

# Resolved once at process start, not per-prompt -- the host this process (and
# therefore run_shell_command) runs on cannot change over the process's lifetime.
_HOST_PLATFORM = platform.platform()
_HOST_ENVIRONMENT = (
    f"This backend process (and therefore `run_shell_command`) runs on: {_HOST_PLATFORM}\n\n"
    "Any command you pass to `run_shell_command` must use this OS's own conventions, not "
    "Linux's -- e.g. on FreeBSD: `service <name> status/start/stop/restart`, not `systemctl`; "
    "`pkg`, not `apt`/`yum`; BSD-flavored `ps`/`sed`/`ifconfig` flags, not GNU's; no `/proc`. "
    "Check the platform string above before assuming Linux syntax."
)

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
    rendering convention as DEVICE DATABASE/ROUTINES DATABASE. Placed last in
    the assembled prompt (see system_prompt.md), after the databases, since
    it changes every turn and would otherwise bust the prompt-cache prefix
    for everything after it (see claude_adapter.py's system cache_control)."""
    if not time_data:
        return "```python\n# Time/timezone/location information is unavailable.\n```"
    lines = [
        f"{var_name} = {time_data[key]!r}" for key, var_name in _TIME_INFO_VARS if time_data.get(key) is not None
    ]
    return f"```python\n{chr(10).join(lines)}\n```"


def _render_preference_aliases(nucore_interface: NuCoreInterface) -> str:
    """Render every stored alias as a Python dict literal, same convention as
    TIME & LOCATION/DEVICE DATABASE. Events are deliberately not rendered
    here -- see list_preferences -- only aliases are small/static enough to
    justify a standing, always-on slot."""
    store = get_store(nucore_interface)
    if store is None:
        return "```python\n# Preferences are not configured for this installation.\n```"

    aliases = store.list("alias")
    if not aliases:
        return "```python\n# No aliases saved yet.\n```"

    lines = ["ALIASES = {"]
    lines += [f"  {a['alias']!r}: {a['target']!r}," for a in aliases]
    lines.append("}")
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
    preference_aliases = _render_preference_aliases(nucore_interface)

    prompt = system_prompt.replace("<<definitions>>", definitions)
    prompt = prompt.replace("<<host_environment>>", _HOST_ENVIRONMENT)
    prompt = prompt.replace("<<ui_navigation_rules>>", ui_navigation_rules)
    prompt = prompt.replace("<<device_database>>", device_database)
    prompt = prompt.replace("<<routines_database>>", routines_database)
    prompt = prompt.replace("<<time_info>>", time_info)
    prompt = prompt.replace("<<preference_aliases>>", preference_aliases)
    return prompt

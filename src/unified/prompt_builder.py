"""Assembles the unified system prompt, as cache-friendly sections.

Reads ``nucore_interface``/``rag`` directly to build the compact
``DEVICE DATABASE``/``ROUTINES DATABASE`` sections -- no config-file/
directory-loading machinery involved. system_prompt.md carries two
``<<cache_boundary>>`` markers, splitting it into three ordered,
least-to-most-volatile sections:

1. CRITICAL RULES + concept sections (Devices, Groups and scenes, Folders,
   Variables, Routines, Plugins) + UI rules + host_environment + DEVICE
   DATABASE -- genuinely static: DEVICE DATABASE only changes on a
   structural device edit (add/remove/rename/enable-disable/error), never on
   a plain status change (confirmed against IoXWrapper's
   ``device_structure_changed`` gate). CRITICAL RULES sits at the very top
   on purpose: it has no per-turn substitution, so it belongs in the most
   stable block, and smaller models weight the start of the prompt most.
2. ROUTINES DATABASE alone -- changes far more often than (1), since
   authoring/editing/deleting a routine via chat is a routine (no pun
   intended) customer action, not a rare structural edit. Giving it a
   separate breakpoint means editing one routine only costs a rewrite of
   this small block plus the tail, not the ~24K-token block of otherwise-
   stable prose in (1) too -- confirmed against live cache-usage logs, where
   routine edits (not device on/off toggles, which don't touch DEVICE
   DATABASE at all) were the actual cause of the partial cache misses this
   split fixes.
3. USER PREFERENCES + TIME & LOCATION + a two-line REMINDER -- changes every
   turn. The REMINDER is static text, but it is deliberately last so the
   core rules are also the most recent thing the model reads before the
   conversation; it costs a few tokens in a block that is rewritten on
   every date rollover anyway.

Each section is sent as its own system message so claude_adapter can give
each its own prompt-cache breakpoint -- see build_system_prompt_sections.
"""

from __future__ import annotations

import datetime
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

# (time_data key, rendered Python variable name), in display order. Only
# the facts needed constantly (which day it is, which timezone every other
# already-local timestamp in the system is in) -- NOT current_time (full
# precision) or sunrise/sunset, which are cheap enough to fetch but not
# needed often enough to justify standing in every turn's prompt; see
# get_time_info (tool_time_get_info.json) for those instead.
_TIME_INFO_VARS = (
    ("timezone", "TIMEZONE"),
    ("latitude", "LATITUDE"),
    ("longitude", "LONGITUDE"),
)


def _render_time_info(time_data: dict[str, Any] | None) -> str:
    """Render TIMEZONE/LATITUDE/LONGITUDE plus a derived CURRENT_DATE (just
    the date portion of get_timespecs()'s current_time -- the full
    timestamp and sunrise/sunset live behind get_time_info instead, a tool
    call away, not standing context) as Python literals -- same rendering
    convention as DEVICE DATABASE/ROUTINES DATABASE. Lives after the
    ``<<cache_boundary>>`` marker in system_prompt.md, in the volatile tail
    section, since it changes every turn -- kept out of the static section
    so it doesn't bust that section's prompt-cache entry (see
    build_system_prompt_sections and claude_adapter.py's system blocks)."""
    if not time_data:
        return "```python\n# Time/timezone/location information is unavailable.\n```"
    lines = [
        f"{var_name} = {time_data[key]!r}" for key, var_name in _TIME_INFO_VARS if time_data.get(key) is not None
    ]
    current_time = time_data.get("current_time")
    if current_time:
        current_date = datetime.datetime.fromisoformat(current_time).date().isoformat()
        lines.append(f"CURRENT_DATE = {current_date!r}")
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


_CACHE_BOUNDARY = "<<cache_boundary>>"


async def build_system_prompt_sections(nucore_interface: NuCoreInterface) -> list[str]:
    """Build the unified system prompt as ordered sections: [static +
    DEVICE DATABASE, ROUTINES DATABASE, volatile tail] -- see this module's
    docstring for why ROUTINES DATABASE gets its own section.

    The template is split on ``<<cache_boundary>>`` *before* substitution
    (so a substituted value can never contain the marker), then each part
    gets the same placeholder substitution. Callers send each section as
    its own system message.
    """
    await nucore_interface._refresh_routines_database()

    template = (_PROMPT_DIR / "system_prompt.md").read_text(encoding="utf-8").strip()
    parts = template.split(_CACHE_BOUNDARY)
    if len(parts) != 3:
        raise ValueError(
            f"system_prompt.md must contain exactly two {_CACHE_BOUNDARY} markers, found {len(parts) - 1}"
        )

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

    substitutions = {
        "<<host_environment>>": _HOST_ENVIRONMENT,
        "<<device_database>>": device_database,
        "<<routines_database>>": routines_database,
        "<<time_info>>": time_info,
        "<<preference_aliases>>": preference_aliases,
    }
    sections: list[str] = []
    for part in parts:
        for placeholder, value in substitutions.items():
            part = part.replace(placeholder, value)
        sections.append(part.strip())
    return sections


async def build_system_prompt(nucore_interface: NuCoreInterface) -> str:
    """The sections from build_system_prompt_sections joined into one string."""
    return "\n\n".join(await build_system_prompt_sections(nucore_interface))

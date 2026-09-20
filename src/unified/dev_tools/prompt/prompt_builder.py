"""Builds the developer tool set's system prompt sections. Deliberately not
unified.prompt_builder.build_system_prompt_sections -- that function
assembles NuCore-customer-specific sections (DEVICE DATABASE, ROUTINES
DATABASE, preference aliases) that don't belong in a developer prompt. Only
the general shape is reused (return a ``list[str]`` of sections for
AgenticLoop.run), not that function's data sources.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nucore import NuCoreInterface

_PROMPT_DIR = Path(__file__).parent


async def build_system_prompt_sections(nucore_interface: NuCoreInterface) -> list[str]:
    template = (_PROMPT_DIR / "system_prompt.md").read_text(encoding="utf-8")
    sections = [template]

    installed_section = await _build_installed_plugins_section(nucore_interface)
    if installed_section:
        sections.append(installed_section)

    return sections


async def _build_installed_plugins_section(nucore_interface: NuCoreInterface) -> str | None:
    """Best-effort -- an installed-plugins listing is a convenience, not a
    requirement; any failure here just means the assistant calls
    list_installed_plugins itself instead of already knowing."""
    try:
        response: Any = await nucore_interface.get_installed_plugins()
    except Exception:
        return None
    if not isinstance(response, dict) or not response.get("successful"):
        return None
    plugins = response.get("data")
    if not isinstance(plugins, list) or not plugins:
        return None

    lines = ["CURRENTLY INSTALLED PLUGINS (for reference -- call list_installed_plugins for live state):"]
    for p in plugins:
        lines.append(f"- {p.get('name')} (plugin_id={p.get('profileNum')}, state={p.get('state')})")
    return "\n".join(lines)

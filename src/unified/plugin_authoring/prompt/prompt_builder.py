"""Builds the developer tool set's system prompt sections. Deliberately not
unified.prompt_builder.build_system_prompt_sections -- that function
assembles NuCore-customer-specific sections (DEVICE DATABASE, ROUTINES
DATABASE, preference aliases) that don't belong in a developer prompt. Only
the general shape is reused (return a ``list[str]`` of sections for
AgenticLoop.run), not that function's data sources.
"""

from __future__ import annotations

from pathlib import Path

from nucore import NuCoreInterface

from ... import host_identity

_PROMPT_DIR = Path(__file__).parent


async def build_system_prompt_sections(nucore_interface: NuCoreInterface) -> list[str]:
    template = (_PROMPT_DIR / "system_prompt.md").read_text(encoding="utf-8")
    # Identity shares the template's block (as unified's <<host_environment>>
    # does) instead of being its own section: each section is a separate
    # system block, and one more of them exhausts Claude's 4-breakpoint cap,
    # silently disabling the conversation cache breakpoint.
    identity = await host_identity.get_identity_text(nucore_interface)
    # Deliberately no installed-plugins section: its text changed whenever a
    # plugin was installed/started/stopped, and as the last block before the
    # messages that rewrote the whole conversation cache. The model calls
    # list_installed_plugins for live state instead.
    return [f"{template.rstrip()}\n\n{identity}"]

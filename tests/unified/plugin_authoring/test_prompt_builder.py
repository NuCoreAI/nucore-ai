"""plugin_authoring.build_system_prompt_sections -- just the static
system_prompt.md plus an optional best-effort installed-plugins section
(see prompt_builder.py's own docstring for why it's not
unified.prompt_builder.build_system_prompt_sections).
"""

from __future__ import annotations

import pytest

from unified.plugin_authoring import build_system_prompt_sections


@pytest.mark.asyncio
async def test_build_system_prompt_sections_mentions_list_chat_commands():
    # Developers need to discover /customer and the session-management
    # commands too -- no get_installed_plugins on this stub, so the
    # best-effort installed-plugins section is skipped entirely and the
    # static template is the only section returned.
    sections = await build_system_prompt_sections(object())

    assert len(sections) == 1
    assert "## Chat commands" in sections[0]
    assert "list_chat_commands" in sections[0]

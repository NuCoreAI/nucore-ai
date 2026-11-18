"""plugin_authoring.build_system_prompt_sections -- the static
system_prompt.md with the standing OS/product-identity text (see
host_identity.py) folded into the same block, and an optional best-effort installed-plugins section
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
    # best-effort installed-plugins section is skipped entirely, leaving
    # just the one template+identity block.
    sections = await build_system_prompt_sections(object())

    assert len(sections) == 1
    assert "## Chat commands" in sections[0]
    assert "list_chat_commands" in sections[0]


@pytest.mark.asyncio
async def test_build_system_prompt_sections_includes_identity_text():
    # No get_full_system_config on this stub, so the identity text falls
    # back to its "unknown" wording rather than raising. It shares the
    # template's block rather than being its own section -- a separate
    # section would cost a cache breakpoint (see prompt_builder.py).
    sections = await build_system_prompt_sections(object())

    assert "real product is unknown" in sections[0]
    assert "never guess or hedge" in sections[0]

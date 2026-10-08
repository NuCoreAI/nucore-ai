"""make_switch_handler -- the model-driven half of dynamic tool-set
switching (see design/developers/merged-toolsets.md). Success and both
failure modes (disabled target, exhausted cap) are all ordinary tool-call
outcomes -- no bespoke error path.
"""

from __future__ import annotations

import pytest

from unified.loop import SWITCH_TOOL_SET_KEY
from unified.tool_set_switch import make_switch_handler


@pytest.mark.asyncio
async def test_enabled_target_under_cap_returns_the_switch_sentinel():
    handler = make_switch_handler(
        "plugin_authoring", is_target_enabled=lambda: True, get_switch_count=lambda: 0, max_switches=1
    )
    result = await handler(None, {})
    assert result == {SWITCH_TOOL_SET_KEY: "plugin_authoring"}


@pytest.mark.asyncio
async def test_disabled_target_returns_a_plain_error_not_the_sentinel():
    handler = make_switch_handler(
        "plugin_authoring", is_target_enabled=lambda: False, get_switch_count=lambda: 0, max_switches=1
    )
    result = await handler(None, {})
    assert "error" in result
    assert SWITCH_TOOL_SET_KEY not in result
    assert "not enabled" in result["error"]


@pytest.mark.asyncio
async def test_switch_count_at_cap_returns_a_plain_error_not_the_sentinel():
    handler = make_switch_handler(
        "plugin_authoring", is_target_enabled=lambda: True, get_switch_count=lambda: 1, max_switches=1
    )
    result = await handler(None, {})
    assert "error" in result
    assert SWITCH_TOOL_SET_KEY not in result
    assert "maximum number of times" in result["error"]


@pytest.mark.asyncio
async def test_switch_count_under_cap_still_succeeds():
    handler = make_switch_handler(
        "unified", is_target_enabled=lambda: True, get_switch_count=lambda: 1, max_switches=3
    )
    result = await handler(None, {})
    assert result == {SWITCH_TOOL_SET_KEY: "unified"}


@pytest.mark.asyncio
async def test_disabled_check_wins_over_cap_check():
    # A disabled target should be refused even if the cap hasn't been hit --
    # the two checks are independent gates, not an either-or.
    handler = make_switch_handler(
        "plugin_authoring", is_target_enabled=lambda: False, get_switch_count=lambda: 0, max_switches=5
    )
    result = await handler(None, {})
    assert "not enabled" in result["error"]

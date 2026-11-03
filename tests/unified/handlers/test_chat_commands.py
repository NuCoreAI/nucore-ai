"""End-to-end: list_chat_commands dispatched through execute_tool -- the
only way the model can learn about /developer//customer and the seven
session-management commands, all of which are intercepted as literal
strings before ever reaching the LLM/tool-calling layer (see
run_unified_runtime.EisyUIContext._apply_switch_command_or_return).
"""

from __future__ import annotations

import pytest

from unified.dispatch import execute_tool

_EXPECTED_MODE_COMMANDS = {"/developer", "/customer"}
_EXPECTED_SESSION_COMMANDS = {
    "/sessions",
    "/new-session",
    "/load-session",
    "/rename-session",
    "/delete-session",
    "/delete-all-sessions",
    "/compact-session",
}


@pytest.mark.asyncio
async def test_list_chat_commands_has_no_backend_dependency():
    # Purely static -- nucore_interface is never touched, so None is fine.
    result = await execute_tool("list_chat_commands", {}, nucore_interface=None)
    assert "error" not in result


@pytest.mark.asyncio
async def test_list_chat_commands_includes_every_mode_command_with_usage():
    result = await execute_tool("list_chat_commands", {}, nucore_interface=None)

    commands = {c["command"]: c for c in result["mode_commands"]}
    assert set(commands) == _EXPECTED_MODE_COMMANDS
    for command, entry in commands.items():
        assert entry["usage"] == command  # no args for either mode command


@pytest.mark.asyncio
async def test_list_chat_commands_mode_note_points_at_the_switch_tools():
    result = await execute_tool("list_chat_commands", {}, nucore_interface=None)

    note = result["mode_commands_note"]
    assert "switch_to_developer_mode" in note
    assert "switch_to_customer_mode" in note


@pytest.mark.asyncio
async def test_list_chat_commands_includes_every_session_command_with_usage():
    result = await execute_tool("list_chat_commands", {}, nucore_interface=None)

    commands = {c["command"]: c for c in result["session_commands"]}
    assert set(commands) == _EXPECTED_SESSION_COMMANDS
    assert commands["/load-session"]["usage"] == "/load-session <id>"
    assert commands["/rename-session"]["usage"] == "/rename-session <id> <name>"
    assert commands["/sessions"]["usage"] == "/sessions"


@pytest.mark.asyncio
async def test_list_chat_commands_session_note_says_there_is_no_tool():
    result = await execute_tool("list_chat_commands", {}, nucore_interface=None)

    note = result["session_commands_note"]
    assert "no tool" in note.lower()

"""``run_shell_command`` -- calls the handler directly for behavior that
doesn't touch ``nucore_interface`` (unused by this tool), and goes through
``execute_tool`` once to confirm the ``TOOL_HANDLERS`` dispatch wiring.
"""

from __future__ import annotations

import os

import pytest

from unified.handlers import shell
from unified.dispatch import execute_tool

from .test_plugin_management import FakeBackend


@pytest.mark.asyncio
async def test_success_returns_stdout_and_zero_exit():
    result = await shell.run_shell_command(None, {"command": "echo hello"})
    assert result["exit_code"] == 0
    assert result["stdout"] == "hello\n"
    assert result["timed_out"] is False
    assert result["truncated"] is False
    assert "error" not in result


@pytest.mark.asyncio
async def test_failing_command_is_data_not_a_tool_error():
    result = await shell.run_shell_command(None, {"command": "ls /definitely/not/a/real/path"})
    assert result["exit_code"] != 0
    assert result["stderr"] != ""
    assert "error" not in result


@pytest.mark.asyncio
async def test_command_not_found_is_exit_127():
    result = await shell.run_shell_command(None, {"command": "totally_not_a_real_binary_xyz"})
    assert result["exit_code"] == 127
    assert "error" not in result


@pytest.mark.asyncio
async def test_timeout_kills_the_process_and_reports_it():
    result = await shell.run_shell_command(None, {"command": "sleep 5", "timeout_s": 1})
    assert result["timed_out"] is True
    assert result["exit_code"] is None
    assert result["duration_s"] < 3

    # confirm it was actually reaped, not left running in the background
    check = await shell.run_shell_command(None, {"command": "pgrep -f 'sleep 5'"})
    assert check["stdout"].strip() == ""


@pytest.mark.asyncio
async def test_large_output_is_truncated_at_exact_boundary():
    result = await shell.run_shell_command(None, {"command": "yes | head -c 5000000"})
    assert result["truncated"] is True
    assert len(result["stdout"].encode("utf-8")) <= shell._MAX_OUTPUT_BYTES


@pytest.mark.asyncio
async def test_invalid_cwd_is_a_handler_error_without_spawning(monkeypatch):
    called = False

    async def fake_create_subprocess_shell(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("should not be called for an invalid cwd")

    monkeypatch.setattr(shell.asyncio, "create_subprocess_shell", fake_create_subprocess_shell)

    result = await shell.run_shell_command(None, {"command": "pwd", "cwd": "/no/such/dir"})
    assert "error" in result
    assert called is False


@pytest.mark.asyncio
async def test_env_is_scrubbed_not_inherited():
    os.environ["_SHELL_TOOL_TEST_SENTINEL"] = "should-not-leak"
    try:
        result = await shell.run_shell_command(None, {"command": "env"})
    finally:
        del os.environ["_SHELL_TOOL_TEST_SENTINEL"]
    assert "_SHELL_TOOL_TEST_SENTINEL" not in result["stdout"]


@pytest.mark.asyncio
async def test_missing_command_is_a_handler_error():
    result = await shell.run_shell_command(None, {})
    assert "error" in result


@pytest.mark.asyncio
async def test_dispatch_wiring_registers_run_shell_command():
    backend = FakeBackend()
    result = await execute_tool("run_shell_command", {"command": "echo ok"}, nucore_interface=backend)
    assert result["exit_code"] == 0
    assert result["stdout"] == "ok\n"

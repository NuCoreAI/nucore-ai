"""setup_dev_venv -- local/dev-testing only: creates/resets a per-plugin
.venv and installs requirements.txt into it. shell.run_shell_command's
actual subprocess execution is monkeypatched throughout -- genuinely
creating a venv is slow and not what this unit test is checking; the
handler's own validation/plumbing is.
"""

from __future__ import annotations

import pytest

from unified.plugin_authoring.handlers import dev_venv
from unified.plugin_authoring.handlers.dev_venv import setup_dev_venv


def _write_requirements(plugin_dir):
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "requirements.txt").write_text("udi_interface>=3.0.57\n")


@pytest.mark.asyncio
async def test_requires_location(tmp_path):
    result = await setup_dev_venv(None, {}, plugin_output_root=str(tmp_path))
    assert "error" in result


@pytest.mark.asyncio
async def test_rejects_path_traversal(tmp_path):
    result = await setup_dev_venv(None, {"location": "../escape"}, plugin_output_root=str(tmp_path))
    assert "error" in result


@pytest.mark.asyncio
async def test_missing_plugin_directory(tmp_path):
    result = await setup_dev_venv(None, {"location": "nope"}, plugin_output_root=str(tmp_path))
    assert "error" in result


@pytest.mark.asyncio
async def test_missing_requirements_txt(tmp_path):
    (tmp_path / "acme_pool").mkdir()
    result = await setup_dev_venv(None, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert "error" in result
    assert "generate_plugin_scaffold" in result["error"]


@pytest.mark.asyncio
async def test_success_creates_venv_and_returns_its_path(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "acme_pool"
    _write_requirements(plugin_dir)

    calls = []

    async def fake_run_shell_command(nucore_interface, args):
        calls.append(args)
        venv_bin = plugin_dir / ".venv" / "bin"
        venv_bin.mkdir(parents=True)
        (venv_bin / "python3").write_text("#!/bin/sh\n")
        return {"exit_code": 0, "stdout": "installed", "stderr": ""}

    monkeypatch.setattr(dev_venv.shell, "run_shell_command", fake_run_shell_command)

    result = await setup_dev_venv(None, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["venv_created"] is True
    assert result["venv_path"] == str(plugin_dir / ".venv")
    assert len(calls) == 1
    assert calls[0]["cwd"] == str(plugin_dir)
    assert ".venv" in calls[0]["command"]
    assert "requirements.txt" in calls[0]["command"]


@pytest.mark.asyncio
async def test_nonzero_exit_is_an_error_with_output(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "acme_pool"
    _write_requirements(plugin_dir)

    async def fake_run_shell_command(nucore_interface, args):
        return {"exit_code": 1, "stdout": "", "stderr": "pip: command not found"}

    monkeypatch.setattr(dev_venv.shell, "run_shell_command", fake_run_shell_command)

    result = await setup_dev_venv(None, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert "error" in result
    assert result["stderr"] == "pip: command not found"


@pytest.mark.asyncio
async def test_shell_plumbing_failure_passed_through(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "acme_pool"
    _write_requirements(plugin_dir)

    async def fake_run_shell_command(nucore_interface, args):
        return {"error": "failed to start command: [Errno 2] No such file or directory"}

    monkeypatch.setattr(dev_venv.shell, "run_shell_command", fake_run_shell_command)

    result = await setup_dev_venv(None, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result == {"error": "failed to start command: [Errno 2] No such file or directory"}


def _write_working_venv(plugin_dir):
    venv_bin = plugin_dir / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    python3 = venv_bin / "python3"
    python3.write_text("#!/bin/sh\n")
    python3.chmod(0o755)


@pytest.mark.asyncio
async def test_already_set_up_is_a_no_op(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "acme_pool"
    _write_requirements(plugin_dir)
    _write_working_venv(plugin_dir)

    async def fail_if_called(nucore_interface, args):
        raise AssertionError("run_shell_command should not be called when .venv already works")

    monkeypatch.setattr(dev_venv.shell, "run_shell_command", fail_if_called)

    result = await setup_dev_venv(None, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result == {
        "location": "acme_pool",
        "venv_created": False,
        "already_set_up": True,
        "venv_path": str(plugin_dir / ".venv"),
    }


@pytest.mark.asyncio
async def test_broken_venv_missing_python3_is_rebuilt(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "acme_pool"
    _write_requirements(plugin_dir)
    (plugin_dir / ".venv" / "bin").mkdir(parents=True)  # no python3 inside -- broken/partial

    calls = []

    async def fake_run_shell_command(nucore_interface, args):
        calls.append(args)
        _write_working_venv(plugin_dir)
        return {"exit_code": 0, "stdout": "installed", "stderr": ""}

    monkeypatch.setattr(dev_venv.shell, "run_shell_command", fake_run_shell_command)

    result = await setup_dev_venv(None, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["venv_created"] is True
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_force_rebuilds_an_already_working_venv(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "acme_pool"
    _write_requirements(plugin_dir)
    _write_working_venv(plugin_dir)

    calls = []

    async def fake_run_shell_command(nucore_interface, args):
        calls.append(args)
        _write_working_venv(plugin_dir)
        return {"exit_code": 0, "stdout": "installed", "stderr": ""}

    monkeypatch.setattr(dev_venv.shell, "run_shell_command", fake_run_shell_command)

    result = await setup_dev_venv(
        None, {"location": "acme_pool", "force": True}, plugin_output_root=str(tmp_path)
    )

    assert result["venv_created"] is True
    assert len(calls) == 1

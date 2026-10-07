"""setup_vscode_debug_config -- local/dev-testing only: after install_generated_plugin,
copies the real PG3INIT value from /usr/local/etc/rc.d/plugin_<profileNum> into a
local .iox_env, and writes a .vscode/launch.json pointing at it. _RC_D_DIR is
monkeypatched to a tmp_path directory throughout -- never touches the real
/usr/local/etc/rc.d.
"""

from __future__ import annotations

import json

import pytest

from unified.plugin_authoring.handlers import vscode_debug
from unified.plugin_authoring.handlers.vscode_debug import setup_vscode_debug_config


class FakeBackend:
    def __init__(self):
        self.installed_response = {
            "successful": True,
            "data": [{"profileNum": 7, "nsid": "local.acme_pool", "name": "AcmePool", "state": "running"}],
        }

    async def get_installed_plugins(self):
        return self.installed_response


def _write_server_entry(plugin_dir, entry=None):
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "server_entry.json").write_text(
        json.dumps(entry or {"name": "AcmePool", "type": "python3", "nsid": "local.acme_pool"})
    )


def _write_rc_d_script(rc_d_dir, profile_num, pg3init="eyJhIjoxfQ=="):
    rc_d_dir.mkdir(parents=True, exist_ok=True)
    (rc_d_dir / f"plugin_{profile_num}").write_text(
        "#!/bin/sh\n\nplugin_start_precmd()\n{\n\texport HOME=/home/admin\n"
        f"\texport PG3INIT={pg3init}\n}}\n\nrun_rc_command \"$1\"\n"
    )


@pytest.mark.asyncio
async def test_requires_known_nsid(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir, {"name": "AcmePool"})  # no nsid
    backend = FakeBackend()

    result = await setup_vscode_debug_config(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["stage"] == "lookup"
    assert "nsid" in result["error"]


@pytest.mark.asyncio
async def test_nsid_not_in_installed_list(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir)
    backend = FakeBackend()
    backend.installed_response = {"successful": True, "data": []}

    result = await setup_vscode_debug_config(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["stage"] == "lookup"
    assert "not currently installed" in result["error"]


@pytest.mark.asyncio
async def test_installed_entry_missing_profile_num(tmp_path):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir)
    backend = FakeBackend()
    backend.installed_response = {
        "successful": True,
        "data": [{"nsid": "local.acme_pool", "name": "AcmePool"}],
    }

    result = await setup_vscode_debug_config(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["stage"] == "lookup"
    assert "profileNum" in result["error"]


@pytest.mark.asyncio
async def test_missing_rc_d_script(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir)
    monkeypatch.setattr(vscode_debug, "_RC_D_DIR", tmp_path / "no_such_rc_d_dir")
    backend = FakeBackend()

    result = await setup_vscode_debug_config(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["stage"] == "rc_d"
    assert "plugin_7" in result["error"]


@pytest.mark.asyncio
async def test_rc_d_script_without_pg3init_line(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir)
    rc_d_dir = tmp_path / "rc.d"
    rc_d_dir.mkdir()
    (rc_d_dir / "plugin_7").write_text("#!/bin/sh\necho no pg3init here\n")
    monkeypatch.setattr(vscode_debug, "_RC_D_DIR", rc_d_dir)
    backend = FakeBackend()

    result = await setup_vscode_debug_config(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result["stage"] == "rc_d"
    assert "PG3INIT" in result["error"]


@pytest.mark.asyncio
async def test_success_writes_iox_env_and_launch_json(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir, {"name": "AcmePool", "nsid": "local.acme_pool"})
    rc_d_dir = tmp_path / "rc.d"
    _write_rc_d_script(rc_d_dir, 7, pg3init="eyJhIjoxfQ==")
    monkeypatch.setattr(vscode_debug, "_RC_D_DIR", rc_d_dir)
    backend = FakeBackend()

    result = await setup_vscode_debug_config(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert result == {
        "location": "acme_pool",
        "profile_num": 7,
        "iox_env_path": str(plugin_dir / ".iox_env"),
        "launch_json_path": str(plugin_dir / ".vscode" / "launch.json"),
    }

    iox_env = (plugin_dir / ".iox_env").read_text()
    assert iox_env == "PG3INIT=eyJhIjoxfQ==\n"

    launch_config = json.loads((plugin_dir / ".vscode" / "launch.json").read_text())
    assert launch_config == {
        "version": "0.2.0",
        "configurations": [
            {
                "name": "AcmePool",
                "type": "debugpy",
                "request": "launch",
                "program": "main.py",
                "console": "integratedTerminal",
                "envFile": "${workspaceFolder}/.iox_env",
                "justMyCode": False,
            }
        ],
    }


@pytest.mark.asyncio
async def test_launch_json_name_falls_back_to_location_when_entry_has_no_name(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir, {"nsid": "local.acme_pool"})  # no "name"
    rc_d_dir = tmp_path / "rc.d"
    _write_rc_d_script(rc_d_dir, 7)
    monkeypatch.setattr(vscode_debug, "_RC_D_DIR", rc_d_dir)
    backend = FakeBackend()

    await setup_vscode_debug_config(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    launch_config = json.loads((plugin_dir / ".vscode" / "launch.json").read_text())
    assert launch_config["configurations"][0]["name"] == "acme_pool"


@pytest.mark.asyncio
async def test_second_call_overwrites_both_files_with_fresh_content(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "acme_pool"
    _write_server_entry(plugin_dir)
    rc_d_dir = tmp_path / "rc.d"
    _write_rc_d_script(rc_d_dir, 7, pg3init="old-token")
    monkeypatch.setattr(vscode_debug, "_RC_D_DIR", rc_d_dir)
    backend = FakeBackend()

    await setup_vscode_debug_config(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))
    assert (plugin_dir / ".iox_env").read_text() == "PG3INIT=old-token\n"

    _write_rc_d_script(rc_d_dir, 7, pg3init="new-token")  # simulates a real restart rotating the token
    await setup_vscode_debug_config(backend, {"location": "acme_pool"}, plugin_output_root=str(tmp_path))

    assert (plugin_dir / ".iox_env").read_text() == "PG3INIT=new-token\n"

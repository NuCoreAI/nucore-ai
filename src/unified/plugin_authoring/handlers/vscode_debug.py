"""``setup_vscode_debug_config`` -- local/dev-testing only: after a plugin
has been installed on the real hub (``install_generated_plugin``), writes a
``.iox_env`` file (the installed service's own ``PG3INIT`` token, copied
verbatim from its real ``/usr/local/etc/rc.d/plugin_<profileNum>`` script)
and a ``.vscode/launch.json`` pointing at it, so a developer can attach a
local debugger (debugpy) to the generated plugin with the exact same
``PG3INIT`` identity/MQTT credentials the real daemonized process uses.

Never a side effect of ``install_generated_plugin`` itself -- unlike
``setup_dev_venv``, which that tool *does* now call automatically (see its
own docstring): a venv is useful on every install/test attempt, debugging
isn't. A real production install never has a ``.vscode/`` or ``.iox_env``
here, and a non-technical customer never wants one either -- a developer
calls this explicitly, after installing, when they actually want to debug
the plugin locally.

Always overwrites both files unconditionally (unlike ``setup_dev_venv``,
which skips a working ``.venv``) -- cheap to regenerate, and PG3INIT itself
rotates on every real restart, so a stale ``.iox_env`` would silently hold
a dead token; always refreshing from the live rc.d script is the correct
default here, not a waste to guard against.

Ahead of that unconditional part sits a separate, conditional sub-step: if
the hub's VS Code Remote setup (``NuCoreInterface.setup_eisy_ai_vscode_remote``
-- installs missing packages, configures the environment) hasn't
successfully run yet for this ``plugin_output_root`` (tracked in
``vscode_remote_config.json``, shared across every plugin here since the
hub-side setup isn't plugin-specific), or the caller passes ``force``, this
asks the developer (via a ``needs_confirmation`` response the model relays)
before running it -- see ``vscode_remote_config.py``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nucore import NuCoreInterface

from ..file_change_notify import notify_files_changed
from ..path_confinement import confine_path
from ..permissions_refresh import refresh_permissions_after_write
from ..vscode_remote_config import (
    VSCODE_REMOTE_CONFIG_FILENAME,
    load_vscode_remote_config,
    write_vscode_remote_config,
)
from .install import _is_successful, _read_server_entry

_RC_D_DIR = Path("/usr/local/etc/rc.d")
_PG3INIT_PATTERN = re.compile(r"^\s*export\s+PG3INIT=(\S+)\s*$", re.MULTILINE)
_IOX_ENV_FILENAME = ".iox_env"
_VSCODE_DIRNAME = ".vscode"
_LAUNCH_JSON_FILENAME = "launch.json"
_SSH_USER = "admin"
_SSH_BASH_ENV = ".bash_linux"
_NON_ALNUM_PATTERN = re.compile(r"[^a-zA-Z0-9]+")


def _sanitize_host_alias(name: str) -> str:
    slug = _NON_ALNUM_PATTERN.sub("-", name).strip("-").lower()
    return slug or "eisy"


def _build_vscode_remote_instructions(full_config: dict[str, Any] | None) -> dict[str, Any]:
    friendly_name = (full_config or {}).get("Friendly Name") or ""
    host_ip = (full_config or {}).get("Network Interface IP") or ""
    host_alias = f"eisy-{_sanitize_host_alias(friendly_name)}" if friendly_name else "eisy-<meaningful-name>"
    hostname = host_ip or "<your eisy's IP address -- check the Admin Console if this wasn't auto-detected>"
    snippet = f'Host {host_alias}\n  HostName {hostname}\n  User {_SSH_USER}\n  SetEnv BASH_ENV="{_SSH_BASH_ENV}"\n'
    return {
        "steps": [
            "Start VS Code on your local computer.",
            "Install the Remote - SSH extension locally.",
            "In your local computer's home directory, inside the .ssh directory, create (or append "
            "to) a file named 'config' (no file extension) with the following contents:",
            "Reload/restart VS Code, then use the Remote-SSH extension to connect to the new host.",
        ],
        "ssh_config_path": "~/.ssh/config",
        "ssh_config_snippet": snippet,
        "host_alias": host_alias,
        "hostname": hostname,
        "user": _SSH_USER,
    }


async def setup_vscode_debug_config(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, plugin_output_root: str
) -> Any:
    location = (args.get("location") or "").strip()
    result = _read_server_entry(plugin_output_root, location)
    if isinstance(result, dict):
        return result
    entry_path, entry = result
    plugin_dir = entry_path.parent

    nsid = entry.get("nsid")
    if not (isinstance(nsid, str) and nsid):
        return {
            "stage": "lookup",
            "error": f"'{location}' has no known nsid -- call install_generated_plugin first",
        }

    installed_response = await nucore_interface.get_installed_plugins()
    installed_list = installed_response.get("data") if _is_successful(installed_response) else None
    match = next((p for p in (installed_list or []) if p.get("nsid") == nsid), None)
    if match is None:
        return {
            "stage": "lookup",
            "error": f"'{location}' (nsid '{nsid}') is not currently installed -- call install_generated_plugin first",
        }

    profile_num = match.get("profileNum")
    if not isinstance(profile_num, int):
        return {"stage": "lookup", "error": f"installed-plugins entry for nsid '{nsid}' has no profileNum"}

    force = bool(args.get("force"))
    confirm_vscode_remote = args.get("confirm_vscode_remote")  # None | True | False

    vscode_remote_config = load_vscode_remote_config(plugin_output_root)
    already_enabled = bool(vscode_remote_config and vscode_remote_config.get("vscode_remote_enabled"))
    needs_vscode_remote_setup = force or not already_enabled

    vscode_remote_result: dict[str, Any] | None = None
    vscode_remote_config_written = False
    if needs_vscode_remote_setup:
        if confirm_vscode_remote is None:
            return {
                "stage": "vscode_remote_confirm",
                "needs_confirmation": True,
                "location": location,
                "question": (
                    "You asked to force VS Code Remote setup to run again"
                    if already_enabled
                    else "VS Code Remote development isn't set up on this hub yet"
                )
                + " -- this installs any missing packages and configures the environment the hub "
                "needs for VS Code Remote-SSH. Set it up now?",
            }
        if confirm_vscode_remote:
            remote_response = await nucore_interface.setup_eisy_ai_vscode_remote()
            succeeded = isinstance(remote_response, dict) and "error" not in remote_response
            write_vscode_remote_config(plugin_output_root, enabled=succeeded)
            vscode_remote_config_written = True
            vscode_remote_result = {"attempted": True, "succeeded": succeeded}
            if succeeded:
                full_config = await nucore_interface.get_full_system_config()
                vscode_remote_result["instructions"] = _build_vscode_remote_instructions(full_config)
            else:
                vscode_remote_result["error"] = (
                    remote_response.get("error") if isinstance(remote_response, dict) else str(remote_response)
                )
        else:
            vscode_remote_result = {"attempted": False}

    rc_d_path = _RC_D_DIR / f"plugin_{profile_num}"
    try:
        rc_d_text = rc_d_path.read_text(encoding="utf-8")
    except OSError as exc:
        return {
            "stage": "rc_d",
            "error": f"could not read '{rc_d_path}' (profileNum {profile_num}): {exc}",
        }

    pg3init_match = _PG3INIT_PATTERN.search(rc_d_text)
    if pg3init_match is None:
        return {"stage": "rc_d", "error": f"no 'export PG3INIT=...' line found in '{rc_d_path}'"}
    pg3init_value = pg3init_match.group(1)

    iox_env_path = plugin_dir / _IOX_ENV_FILENAME
    iox_env_path.write_text(f"PG3INIT={pg3init_value}\n", encoding="utf-8")

    vscode_dir = plugin_dir / _VSCODE_DIRNAME
    vscode_dir.mkdir(exist_ok=True)
    launch_json_path = vscode_dir / _LAUNCH_JSON_FILENAME
    launch_config = {
        "version": "0.2.0",
        "configurations": [
            {
                "name": entry.get("name") or location,
                "type": "debugpy",
                "request": "launch",
                "program": "main.py",
                "console": "integratedTerminal",
                "envFile": f"${{workspaceFolder}}/{_IOX_ENV_FILENAME}",
                "justMyCode": False,
            }
        ],
    }
    launch_json_path.write_text(json.dumps(launch_config, indent=4) + "\n", encoding="utf-8")

    changed_paths = [str(iox_env_path), str(launch_json_path)]
    if vscode_remote_config_written:
        changed_paths.append(str(confine_path(plugin_output_root, VSCODE_REMOTE_CONFIG_FILENAME)))

    permissions_refresh_error = await refresh_permissions_after_write(nucore_interface)
    files_changed_notify_error = await notify_files_changed(nucore_interface, changed_paths)

    result = {
        "location": location,
        "profile_num": profile_num,
        "iox_env_path": str(iox_env_path),
        "launch_json_path": str(launch_json_path),
    }
    if vscode_remote_result is not None:
        result["vscode_remote"] = vscode_remote_result
    if permissions_refresh_error is not None:
        result["permissions_refresh_error"] = permissions_refresh_error
    if files_changed_notify_error is not None:
        result["files_changed_notify_error"] = files_changed_notify_error
    return result

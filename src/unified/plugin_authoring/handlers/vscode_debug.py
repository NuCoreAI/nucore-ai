"""``setup_vscode_debug_config`` -- local/dev-testing only: after a plugin
has been installed on the real hub (``install_generated_plugin``), writes a
``.iox_env`` file (the installed service's own ``PG3INIT`` token, copied
verbatim from its real ``/usr/local/etc/rc.d/plugin_<profileNum>`` script)
and a ``.vscode/launch.json`` pointing at it, so a developer can attach a
local debugger (debugpy) to the generated plugin with the exact same
``PG3INIT`` identity/MQTT credentials the real daemonized process uses.

Never a side effect of ``install_generated_plugin`` itself, same reasoning
as ``setup_dev_venv``: a real production install never has a ``.vscode/``
or ``.iox_env`` here, and a non-technical customer never wants one either --
a developer calls this explicitly, after installing, when they actually
want to debug the plugin locally.

Always overwrites both files unconditionally (unlike ``setup_dev_venv``,
which skips a working ``.venv``) -- cheap to regenerate, and PG3INIT itself
rotates on every real restart, so a stale ``.iox_env`` would silently hold
a dead token; always refreshing from the live rc.d script is the correct
default here, not a waste to guard against.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nucore import NuCoreInterface

from .install import _is_successful, _read_server_entry

_RC_D_DIR = Path("/usr/local/etc/rc.d")
_PG3INIT_PATTERN = re.compile(r"^\s*export\s+PG3INIT=(\S+)\s*$", re.MULTILINE)
_IOX_ENV_FILENAME = ".iox_env"
_VSCODE_DIRNAME = ".vscode"
_LAUNCH_JSON_FILENAME = "launch.json"


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

    return {
        "location": location,
        "profile_num": profile_num,
        "iox_env_path": str(iox_env_path),
        "launch_json_path": str(launch_json_path),
    }

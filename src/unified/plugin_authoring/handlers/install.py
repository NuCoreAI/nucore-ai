"""``install_generated_plugin`` (design/developers/plugin_authoring_p4_impl.md
Stage 3): the real register -> install -> start pipeline for a plugin
``generate_plugin_scaffold`` has already written to disk. Deliberately a
separate tool from ``generate_plugin_scaffold``, not a fused step -- lets
the model (and the customer) review the generated artifacts before
anything touches the real hub, and lets this tool be re-called alone if
only the install/start step failed.

Each stage's failure is reported distinctly via a ``"stage"`` key
(``"read"``/``"register"``/``"install"``/``"start"``) -- no silent retry,
no partial success reported as full success.
"""

from __future__ import annotations

import json
from typing import Any

from nucore import NuCoreInterface

from ..path_confinement import confine_path

_SERVER_ENTRY_FILENAME = "server_entry.json"


def _is_successful(response: Any) -> bool:
    return isinstance(response, dict) and bool(response.get("successful"))


async def install_generated_plugin(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, plugin_output_root: str
) -> Any:
    location = (args.get("location") or "").strip()
    if not location:
        return {"stage": "read", "error": "location is required"}

    try:
        plugin_dir = confine_path(plugin_output_root, location)
    except ValueError as exc:
        return {"stage": "read", "error": str(exc)}

    entry_path = plugin_dir / _SERVER_ENTRY_FILENAME
    if not entry_path.is_file():
        return {
            "stage": "read",
            "error": f"no {_SERVER_ENTRY_FILENAME} found at '{location}' -- call generate_plugin_scaffold first",
        }
    try:
        entry = json.loads(entry_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"stage": "read", "error": f"{_SERVER_ENTRY_FILENAME} at '{location}' is not valid JSON: {exc}"}

    nsid = f"local.{location}"

    register_response = await nucore_interface.register_local_plugin(entry)
    if not _is_successful(register_response):
        return {"stage": "register", "error": f"failed to register plugin '{location}' (nsid '{nsid}')"}

    install_response = await nucore_interface.install_local_plugin(nsid)
    if not _is_successful(install_response):
        return {"stage": "install", "error": f"failed to install plugin '{nsid}'"}

    profile_num = (install_response.get("data") or {}).get("profileNum")
    if not isinstance(profile_num, int):
        return {"stage": "install", "error": f"install of '{nsid}' did not return a profileNum"}

    installed_response = await nucore_interface.get_installed_plugins()
    installed = installed_response.get("data") if _is_successful(installed_response) else None
    match = next((p for p in (installed or []) if p.get("profileNum") == profile_num), None)
    if match is None:
        return {
            "stage": "install",
            "error": f"installed plugin '{nsid}' (profileNum {profile_num}) not found in list_installed_plugins",
        }

    start_response = await nucore_interface.plugin_ops(profile_num, "start")
    if not _is_successful(start_response):
        return {"stage": "start", "error": f"failed to start plugin '{nsid}' (plugin_id {profile_num})"}

    return {
        "location": location,
        "nsid": nsid,
        "plugin_id": profile_num,
        "name": match.get("name"),
        "started": True,
    }

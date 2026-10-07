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

``register_local_plugin`` is create-only ("Creates a new local dev
plugin" -- design/developers/plugin_apis.md). So once a plugin has ever
been registered, its host-assigned ``nsid`` is persisted back into its own
``server_entry.json`` (see ``_persist_entry_with_nsid``) -- never just used
transiently -- and every later ``install_generated_plugin`` call checks
whether that ``nsid`` is still present in the host's local-store
registrations or installed-plugins list *before* acting, refusing with a
``"conflict"`` response rather than silently duplicating a registration or
re-installing over something that's already there. ``update_registered_
plugin``/``delete_registered_plugin`` below are how a reported conflict
gets resolved -- "abort" needs no tool at all, the model/customer just
doesn't call anything further.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nucore import NuCoreInterface

from ..path_confinement import confine_path

_SERVER_ENTRY_FILENAME = "server_entry.json"


def _is_successful(response: Any) -> bool:
    return isinstance(response, dict) and bool(response.get("successful"))


def _strip_nsid(entry: dict[str, Any]) -> dict[str, Any]:
    """The wire body sent to register_local_plugin()/update_local_plugin()
    never carries a locally-persisted ``nsid`` -- the docs are explicit
    it's never part of that request body (host-assigned on register,
    supplied in the URL, not the body, on update)."""
    return {k: v for k, v in entry.items() if k != "nsid"}


def _persist_entry_with_nsid(entry_path: Path, entry_without_nsid: dict[str, Any], nsid: str) -> None:
    entry_path.write_text(json.dumps({**entry_without_nsid, "nsid": nsid}, indent=2) + "\n", encoding="utf-8")


def _read_server_entry(plugin_output_root: str, location: str) -> tuple[Path, dict[str, Any]] | dict[str, Any]:
    """Resolves *location* to ``(entry_path, entry)``, or a ``{"stage":
    "read", "error": ...}`` dict on any failure -- shared by every tool in
    this module so they can't disagree on this."""
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

    return entry_path, entry


async def install_generated_plugin(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, plugin_output_root: str
) -> Any:
    location = (args.get("location") or "").strip()
    result = _read_server_entry(plugin_output_root, location)
    if isinstance(result, dict):
        return result
    entry_path, entry = result

    known_nsid = entry.get("nsid")
    if not (isinstance(known_nsid, str) and known_nsid):
        known_nsid = None
    entry_body = _strip_nsid(entry)

    if known_nsid:
        store_response = await nucore_interface.get_local_store_plugins()
        store_list = store_response.get("data") if _is_successful(store_response) else None
        registered_match = next((p for p in (store_list or []) if p.get("nsid") == known_nsid), None)

        installed_response = await nucore_interface.get_installed_plugins()
        installed_list = installed_response.get("data") if _is_successful(installed_response) else None
        installed_match = next((p for p in (installed_list or []) if p.get("nsid") == known_nsid), None)

        if registered_match or installed_match:
            return {
                "stage": "register",
                "conflict": True,
                "nsid": known_nsid,
                "registered": (
                    {
                        "name": registered_match.get("name"),
                        "type": registered_match.get("type"),
                        "path": registered_match.get("path"),
                        "updated_at": registered_match.get("updatedAt"),
                    }
                    if registered_match
                    else None
                ),
                "installed": (
                    {
                        "plugin_id": installed_match.get("profileNum"),
                        "name": installed_match.get("name"),
                        "state": installed_match.get("state"),
                    }
                    if installed_match
                    else None
                ),
                "message": (
                    f"'{location}' is already known to the host under nsid '{known_nsid}' -- resolve "
                    "explicitly before re-calling install_generated_plugin: update_registered_plugin "
                    "(refresh the registration in place -- also syncs to the installed record "
                    "automatically if installed), delete_registered_plugin and/or "
                    "uninstall_installed_plugin (a harder reset), or do nothing to abort."
                ),
            }
        # known_nsid matches nothing in either list -- stale (e.g. deleted
        # on the host out-of-band). Fall through and register fresh, same
        # as if no nsid had ever been known.

    register_response = await nucore_interface.register_local_plugin(entry_body)
    if not _is_successful(register_response):
        return {"stage": "register", "error": f"failed to register plugin '{location}'"}

    nsid = (register_response.get("data") or {}).get("nsid")
    if not isinstance(nsid, str) or not nsid:
        return {"stage": "register", "error": f"register response for '{location}' did not include a nsid"}

    persist_error: str | None = None
    try:
        _persist_entry_with_nsid(entry_path, entry_body, nsid)
    except Exception as exc:
        # The registration already succeeded on the real host and can't be
        # rolled back -- an unrelated local disk problem (permissions, full
        # disk) shouldn't be reported as a register-stage failure.
        persist_error = str(exc)

    install_response = await nucore_interface.install_local_plugin(nsid)
    if not _is_successful(install_response):
        return {"stage": "install", "error": f"failed to install plugin '{nsid}'"}

    profile_num = (install_response.get("data") or {}).get("profileNum")
    if not isinstance(profile_num, int):
        return {"stage": "install", "error": f"install of '{nsid}' did not return a profileNum"}

    installed_response = await nucore_interface.get_installed_plugins()
    installed = installed_response.get("data") if _is_successful(installed_response) else None
    match = next((p for p in (installed or []) if p.get("nsid") == nsid), None)
    if match is None:
        return {
            "stage": "install",
            "error": f"installed plugin '{nsid}' (profileNum {profile_num}) not found in list_installed_plugins",
        }
    if match.get("profileNum") != profile_num:
        return {
            "stage": "install",
            "error": (
                f"host inconsistency: install_local_plugin returned profileNum {profile_num} but the "
                f"installed-list entry for nsid '{nsid}' has profileNum {match.get('profileNum')}"
            ),
        }

    start_response = await nucore_interface.plugin_ops(profile_num, "start")
    if not _is_successful(start_response):
        return {"stage": "start", "error": f"failed to start plugin '{nsid}' (plugin_id {profile_num})"}

    result = {
        "location": location,
        "nsid": nsid,
        "plugin_id": profile_num,
        "name": match.get("name"),
        "started": True,
    }
    if persist_error is not None:
        result["nsid_persisted"] = False
        result["persist_error"] = persist_error
    return result


async def update_registered_plugin(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, plugin_output_root: str
) -> Any:
    """Resolves a reported ``"conflict"``'s registered half: pushes this
    plugin's current local ``server_entry.json`` to the host via the
    update endpoint, in place -- never calls register_local_plugin(). Per
    the docs this also syncs to the installed record automatically if the
    plugin is installed, but does not itself restart the running process;
    follow with ``plugin_ops(operation="restart")`` if the change needs to
    take effect immediately."""
    location = (args.get("location") or "").strip()
    result = _read_server_entry(plugin_output_root, location)
    if isinstance(result, dict):
        return result
    entry_path, entry = result

    nsid = entry.get("nsid")
    if not (isinstance(nsid, str) and nsid):
        return {
            "stage": "update",
            "error": f"'{location}' has no known nsid -- nothing to update (call install_generated_plugin first)",
        }

    entry_body = _strip_nsid(entry)
    update_response = await nucore_interface.update_local_plugin(nsid, entry_body)
    if not _is_successful(update_response):
        return {"stage": "update", "error": f"failed to update registration for '{location}' (nsid '{nsid}')"}

    _persist_entry_with_nsid(entry_path, entry_body, nsid)

    return {"location": location, "nsid": nsid, "updated": True}


async def delete_registered_plugin(
    nucore_interface: NuCoreInterface, args: dict[str, Any], *, plugin_output_root: str
) -> Any:
    """Resolves a reported ``"conflict"``'s registered half the other way:
    deletes the dev-store registration entirely, then clears the locally-
    known ``nsid`` so the next install_generated_plugin call registers
    fresh. Does not uninstall -- see uninstall_installed_plugin for that."""
    location = (args.get("location") or "").strip()
    result = _read_server_entry(plugin_output_root, location)
    if isinstance(result, dict):
        return result
    entry_path, entry = result

    nsid = entry.get("nsid")
    if not (isinstance(nsid, str) and nsid):
        return {"stage": "delete", "error": f"'{location}' has no known nsid -- nothing to delete"}

    delete_response = await nucore_interface.delete_local_plugin(nsid)
    if not _is_successful(delete_response):
        return {"stage": "delete", "error": f"failed to delete registration for '{location}' (nsid '{nsid}')"}

    entry_path.write_text(json.dumps(_strip_nsid(entry), indent=2) + "\n", encoding="utf-8")

    return {"location": location, "nsid": nsid, "deleted": True}

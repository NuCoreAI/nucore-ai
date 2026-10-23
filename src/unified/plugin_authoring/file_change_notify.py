"""Shared post-write hook: tells the platform (POST /api/plugin/code/
files-changed) every time a handler finishes generating, updating, or
removing a batch of a plugin's on-disk files -- once per batch, never once
per file -- so eisy-ui and friends know to re-read them. Non-fatal by
design, same composition as permissions_refresh.py: files are already on
disk (or already gone) by the time this runs, so a hub-side failure here is
reported back to the caller, never treated as the write/removal itself
having failed.
"""

from __future__ import annotations

from nucore import NuCoreInterface


async def notify_files_changed(nucore_interface: NuCoreInterface, paths: list[str]) -> str | None:
    """Calls ``notify_plugin_files_changed`` once with every changed path in
    this batch; returns an error string to fold into the caller's own result
    dict (e.g. under ``files_changed_notify_error``), or ``None`` on success
    or when ``paths`` is empty (no-op -- nothing changed, nothing to report)."""
    if not paths:
        return None
    response = await nucore_interface.notify_plugin_files_changed(paths)
    if isinstance(response, dict) and "error" in response:
        return response["error"]
    if not isinstance(response, dict):
        return f"unexpected notify_plugin_files_changed response: {response!r}"
    return None

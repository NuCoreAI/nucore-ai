"""Shared post-write hook: ``refresh_eisy_ai_permissions`` must fire once
after a handler finishes writing/rewriting a batch of a plugin's on-disk
files -- never per file -- so the generated plugin's files end up with
usable OS ownership/permissions. Non-fatal by design (mirrors
``handlers/install.py``'s ``setup_dev_venv`` composition): files are already
on disk by the time this runs, so a hub-side failure here is reported back
to the caller, never treated as the write operation itself failing.
"""

from __future__ import annotations

from nucore import NuCoreInterface


async def refresh_permissions_after_write(nucore_interface: NuCoreInterface) -> str | None:
    """Calls ``refresh_eisy_ai_permissions`` once; returns an error string
    to fold into the caller's own result dict (e.g. under
    ``permissions_refresh_error``), or ``None`` on success."""
    response = await nucore_interface.refresh_eisy_ai_permissions()
    if isinstance(response, dict) and "error" in response:
        return response["error"]
    if not isinstance(response, dict):
        return f"unexpected refresh_eisy_ai_permissions response: {response!r}"
    return None

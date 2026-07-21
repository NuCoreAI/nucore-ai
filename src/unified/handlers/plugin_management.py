"""``list_store_plugins``/``list_purchased_plugins`` -- NuCore's own vetted
third-party plugin marketplace (distinct from device/routine/variable data,
and from AI-side tool extensions, which are a separate topic). Plugins here
show up in the device structure as regular nodes once installed (already
handled by the existing device tools) -- these two tools cover the
marketplace/licensing questions that aren't otherwise answerable: what's
available in the store, and what's actually been purchased for this
installation.

Deliberately on-demand (like ``list_variables``), not a standing prompt
database -- store/license state changes rarely and isn't needed on most
turns.
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface


def _data(response: Any) -> list[dict] | None:
    """``get_active_plugins``/``get_purchased_plugins`` return the raw
    ``{"successful": bool, "data": [...]}"`` response JSON, or ``None``/a
    non-2xx ``requests.Response`` on failure -- never raises. Returns the
    ``data`` list only on a genuine success."""
    if response is None or not isinstance(response, dict):
        return None
    if not response.get("successful"):
        return None
    data = response.get("data")
    return data if isinstance(data, list) else None


async def list_store_plugins(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    response = await nucore_interface.get_active_plugins()
    plugins = _data(response)
    if plugins is None:
        return {"error": "failed to fetch the plugin store list"}

    return {
        "plugins": [
            {
                "nsid": p.get("nsid"),
                "name": p.get("name"),
                "author": p.get("author"),
                "description": p.get("desc"),
                "type": p.get("type"),
                "updated_at": p.get("updatedAt"),
            }
            for p in plugins
        ]
    }


async def list_purchased_plugins(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    store_response = await nucore_interface.get_active_plugins()
    store_by_nsid = {p.get("nsid"): p for p in (_data(store_response) or [])}

    licenses_response = await nucore_interface.get_purchased_plugins()
    licenses = _data(licenses_response)
    if licenses is None:
        return {"error": "failed to fetch purchased plugin licenses"}

    return {
        "licenses": [
            {
                "nsid": lic.get("nsid"),
                # None when the licensed plugin is no longer listed in the
                # active store (e.g. discontinued) -- surfaced as-is rather
                # than guessed at.
                "name": (store_by_nsid.get(lic.get("nsid")) or {}).get("name"),
                "edition": lic.get("edition"),
                "active": lic.get("active"),
                "expiry": lic.get("expiry"),
            }
            for lic in licenses
        ]
    }

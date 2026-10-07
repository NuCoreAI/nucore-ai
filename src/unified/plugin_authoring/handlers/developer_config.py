"""``configure_developer`` -- one-time (or update) commissioning of the
developer whose email/name ``generate_plugin_scaffold`` stamps into every
generated ``server_entry.json``'s ``developer``/``author`` fields, and
whose optional ``default_run_as`` becomes that plugin's ``runAs`` fallback
(see ``_DEFAULT_RUN_AS`` in ``handlers/scaffold.py`` for the ultimate
fallback when this isn't set either). The provided email must match this
session's own authenticated identity (``EisyUIContext.get_user_id()``,
threaded through as *get_user_id*) when one is known -- refuses outright
on an actual mismatch. When no authenticated identity is available at all
(e.g. the CLI/REPL path, which has no context-message mechanism to ever
populate one), commissioning is still allowed: the check only ever blocks
a confirmed mismatch, never the absence of an identity to compare against.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from nucore import NuCoreInterface

from .. import developer_config


async def configure_developer(
    nucore_interface: NuCoreInterface,
    args: dict[str, Any],
    *,
    plugin_output_root: str,
    get_user_id: Callable[[], str | None] | None = None,
) -> Any:
    email = (args.get("email") or "").strip()
    name = (args.get("name") or "").strip()
    github_url = (args.get("github_url") or "").strip() or None
    default_run_as = (args.get("default_run_as") or "").strip() or None

    if not email or "@" not in email:
        return {"error": "a valid email is required"}
    if not name:
        return {"error": "name is required"}

    authenticated_user_id = get_user_id() if get_user_id is not None else None
    if authenticated_user_id and authenticated_user_id.strip().lower() != email.lower():
        return {
            "error": f"email '{email}' does not match the authenticated session user "
                     f"'{authenticated_user_id}' -- refusing to write developer_config.json"
        }

    try:
        config = developer_config.write_developer_config(
            plugin_output_root, email=email, name=name, github_url=github_url, default_run_as=default_run_as
        )
    except ValueError as exc:
        return {"error": str(exc)}

    return {"configured": True, **config}

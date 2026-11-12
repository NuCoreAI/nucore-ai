"""``configure_developer`` -- one-time (or update) commissioning of the
developer whose email/name ``generate_plugin_scaffold`` stamps into every
generated ``server_entry.json``'s ``developer``/``author`` fields. The
provided email must match this session's own authenticated identity
(``EisyUIContext.get_user_id()``, threaded through as *get_user_id*) when
one is known -- refuses outright on an actual mismatch, and refuses just as
outright when a login concept applies to this session but nobody is logged
in yet (*get_user_id* is given but returns ``None``): commissioning a new
identity blind, with no account to verify it against, is exactly the gap
that let a mismatched/unverified email slip through before. Only when no
login concept applies to this session at all -- *get_user_id* itself is
``None`` (e.g. the MCP/CLI path, which has no context-message mechanism and
no user-session notion whatsoever) -- does commissioning proceed without an
identity to compare against, since there is nothing to check it against.

Also: get_user_id / "logged in" is deliberately resolved as a 2-level check
-- ``get_user_id is None`` means no login concept applies here at all;
``get_user_id() is None`` means a login concept applies but nobody is
logged in yet. Conflating these would either block the MCP/CLI path (which
never has a login) or let an unauthenticated eisyUI session commission an
identity (which must log in first).

Also ``get_developer_config`` -- the read-only counterpart a caller should
check *first*, every time, before ever calling ``configure_developer``.
Commissioning is meant to happen once per ``plugin_output_root``, for every
plugin generated under it, not once per plugin: without a cheap way to see
what's already on file, a caller (human or model) that can't recall whether
an earlier, separate conversation already commissioned this workspace has
no option but to re-ask/re-call ``configure_developer`` defensively on every
new plugin -- silently clobbering a correct, already-recorded identity with
a different (often less specific, e.g. a stand-in company name instead of
the real one on file) one along the way. This function removes the need for
that guess: a plain read, so ``configure_developer`` is reserved for an
actual first-time setup or a real, explicit identity change.
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

    if not email or "@" not in email:
        return {"error": "a valid email is required"}
    if not name:
        return {"error": "name is required"}

    if get_user_id is not None and get_user_id() is None:
        return {
            "error": "not logged in -- the person must log in to eisyUI before a developer "
                     "identity can be commissioned"
        }

    authenticated_user_id = get_user_id() if get_user_id is not None else None
    if authenticated_user_id and authenticated_user_id.strip().lower() != email.lower():
        return {
            "error": f"email '{email}' does not match the authenticated session user "
                     f"'{authenticated_user_id}' -- refusing to write developer_config.json"
        }

    try:
        config = developer_config.write_developer_config(
            plugin_output_root, email=email, name=name, github_url=github_url
        )
    except ValueError as exc:
        return {"error": str(exc)}

    return {"configured": True, **config}


async def get_developer_config(
    nucore_interface: NuCoreInterface,
    args: dict[str, Any],
    *,
    plugin_output_root: str,
    get_user_id: Callable[[], str | None] | None = None,
) -> Any:
    """Read-only: the ``{email, name, github_url?}`` already on file for
    this *plugin_output_root*, or ``{"configured": False}`` if none has
    been commissioned yet. Never writes anything -- call this before
    ``configure_developer``, not instead of checking it.

    Also surfaces ``authenticated_user_id`` when *get_user_id* is given, so
    a caller learns the known login state up front instead of only via a
    later refusal: absent entirely when no login concept applies to this
    session (e.g. the MCP/CLI path); ``None`` when a login concept applies
    but nobody is logged in yet; the actual email otherwise."""
    config = developer_config.load_developer_config(plugin_output_root)
    result: dict[str, Any] = {"configured": False} if config is None else {"configured": True, **config}
    if get_user_id is not None:
        result["authenticated_user_id"] = get_user_id()
    return result

"""``list_store_plugins``/``list_purchased_plugins``/``list_installed_plugins``
-- NuCore's own vetted third-party plugin marketplace (distinct from
device/routine/variable data, and from AI-side tool extensions, which are a
separate topic). Plugins here show up in the device structure as regular
nodes once installed (already handled by the existing device tools) -- these
tools cover the marketplace/licensing/installation questions that aren't
otherwise answerable: what's available in the store, what's actually been
purchased, and what's actually installed on this device.

Deliberately on-demand (like ``list_variables``), not a standing prompt
database -- store/license state changes rarely and isn't needed on most
turns.

``install_plugin``/``buy_plugin``/``get_plugin_capabilities``/
``call_plugin_tool`` -- the capability-extension mechanism: when no existing
tool covers what the customer wants, the model checks
installed/purchased/store plugins (the three ``list_*`` tools above) in
order, confirms with the customer before installing or buying, then loads a
plugin's own declared capabilities and calls one to actually answer the
request. Install/purchase (``plugin_ops``) are backend stubs today -- no
real install/purchase API exists yet, so these always simulate success. The
plugin-facing get_prompt/get_tools/handle_llm_result calls attempt a real
per-plugin API (see ``NuCoreInterface``'s docstrings); that API may not
exist in production yet either, in which case they fail gracefully
(``successful: false``) rather than raising. See ``design/plan-design.md``'s
"AI-capable plugin contract" for the target shape this implements.
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


async def list_installed_plugins(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    response = await nucore_interface.get_installed_plugins()
    plugins = _data(response)
    if plugins is None:
        return {"error": "failed to fetch installed plugins"}

    return {
        "plugins": [
            {
                # profileNum is this plugin's id for subsequent plugin_ops()/
                # configure_plugin() calls -- surfaced as plugin_id to match
                # those tools' input parameter name.
                "plugin_id": p.get("profileNum"),
                "name": p.get("name"),
                "is_local": p.get("isLocal"),
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


def _op_data(response: Any) -> dict | None:
    """Same as ``_data`` above, but for ``plugin_ops``'s ``{"successful",
    "data": {...}}`` shape, where ``data`` is a single object, not a list."""
    if response is None or not isinstance(response, dict):
        return None
    if not response.get("successful"):
        return None
    data = response.get("data")
    if data is None:
        return None
    return data if isinstance(data, dict) else {"result": data}


async def install_plugin(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    nsid = args.get("nsid")
    if not nsid:
        return {"error": "nsid is required"}

    response = await nucore_interface.plugin_ops(nsid, "install")
    data = _op_data(response)
    if data is None:
        return {"error": f"failed to install plugin '{nsid}'"}

    return {
        "status": "installed",
        "plugin_id": data.get("plugin_id", nsid),
        "stub": True,
        "note": "simulated install -- no real install API exists yet",
    }


async def buy_plugin(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    plugin_id = args.get("plugin_id")
    if not plugin_id:
        return {"error": "plugin_id is required"}

    response = await nucore_interface.plugin_ops(plugin_id, "purchase")
    data = _op_data(response)
    if data is None:
        return {"error": f"failed to purchase plugin '{plugin_id}'"}

    return {
        "status": "purchased_and_installed",
        # No real backend can mint a fresh post-purchase install id, so the
        # same id the customer bought is carried forward as-is -- buying
        # already implies installed, so this is immediately usable with
        # get_plugin_capabilities/call_plugin_tool; no separate
        # install_plugin call needed.
        "plugin_id": data.get("plugin_id", plugin_id),
        "stub": True,
        "note": "simulated purchase+install -- no real purchase API exists yet",
    }


async def get_plugin_capabilities(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    plugin_id = args.get("plugin_id")
    if not plugin_id:
        return {"error": "plugin_id is required"}

    prompt_response = await nucore_interface.get_plugin_prompt(plugin_id)
    prompt_data = _op_data(prompt_response)
    if prompt_data is None:
        return {"error": f"failed to fetch prompt for plugin '{plugin_id}'"}

    tools_response = await nucore_interface.get_plugin_tools(plugin_id)
    tools_data = _op_data(tools_response)
    if tools_data is None:
        return {"error": f"failed to fetch tools for plugin '{plugin_id}'"}

    return {
        "plugin_id": plugin_id,
        "prompt": prompt_data.get("prompt"),
        "tools": tools_data.get("tools", []),
    }


async def call_plugin_tool(nucore_interface: NuCoreInterface, args: dict[str, Any]) -> Any:
    plugin_id = args.get("plugin_id")
    tool_name = args.get("tool_name")
    if not plugin_id:
        return {"error": "plugin_id is required"}
    if not tool_name:
        return {"error": "tool_name is required -- see get_plugin_capabilities' tools"}
    #remove the {plugin_id}_  from tool name
    tool_name = tool_name.split(f"{plugin_id}_", 1)[-1]
    tool_args = args.get("args") or {}
    if not isinstance(tool_args, dict):
        return {"error": f"args must be a JSON object, got {type(tool_args).__name__}"}

    tool_args["tool_name"] = tool_name  # pass the tool name to the plugin for its own dispatching
    response = await nucore_interface.handle_plugin_llm_result(plugin_id, tool_args)
    data = _op_data(response)
    if data is None:
        return {"error": f"plugin '{plugin_id}' failed to run tool '{tool_name}'"}

    return data

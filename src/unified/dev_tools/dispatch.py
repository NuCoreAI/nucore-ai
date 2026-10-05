"""Tool name -> handler dispatch for the developer tool set. Same shape as
unified.dispatch (ToolHandler signature, unknown-tool/exception-to-error-dict
execute_tool), scoped to profile-authoring/UOM-lookup tools plus a curated
subset of unified.handlers.plugin_management's plugin-lifecycle tools --
reused directly, not reimplemented, so the two tool sets never drift on what
"start"/"stop"/"configure a plugin" actually does.

Deliberately excludes buy_plugin/delete_plugin/list_store_plugins/
list_purchased_plugins -- marketplace-only concerns, not part of the local
dev/test loop. Also excludes install_plugin: that handler is the
purchase-flow stub (it hands back a URL rather than installing anything --
see plugin_management.py's module docstring), not a real local-dev install;
there is no tool for that here yet (see design/developers/plugin_dev_tooling.md).
"""

from __future__ import annotations

from typing import Any

from nucore import NuCoreInterface
from utils import get_logger

from ..dispatch import ToolHandler
from ..handlers import plugin_management
from .handlers import profile_authoring

logger = get_logger(__name__)

TOOL_HANDLERS: dict[str, ToolHandler] = {
    "validate_profile": profile_authoring.validate_profile,
    "lookup_uom": profile_authoring.lookup_uom,
    "configure_plugin": plugin_management.configure_plugin,
    "list_installed_plugins": plugin_management.list_installed_plugins,
    "plugin_ops": plugin_management.plugin_ops,
    "get_plugin_capabilities": plugin_management.get_plugin_capabilities,
    "call_plugin": plugin_management.call_plugin,
}


async def execute_tool(name: str, args: dict[str, Any], *, nucore_interface: NuCoreInterface) -> Any:
    """Same contract as unified.dispatch.execute_tool -- never raises."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool '{name}'"}

    try:
        return await handler(nucore_interface, args)
    except Exception as exc:
        logger.error(f"dev tool '{name}' raised: {exc}")
        return {"error": f"'{name}' failed: {exc}"}

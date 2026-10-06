"""Tool name -> handler dispatch for the plugin_authoring tool set. Same shape
as unified.dispatch (ToolHandler signature, unknown-tool/exception-to-error-dict
execute_tool), scoped to profile-authoring/UOM-lookup tools plus a curated
subset of unified.handlers.plugin_management's plugin-lifecycle tools, plus
the customer tool set's run_shell_command (Stage 6, design/developers/
plugin_authoring_p4_impl.md -- hardware/USB detection needs real shell
access) -- all reused directly, not reimplemented, so the tool sets never
drift on what "start"/"stop"/"configure a plugin"/"run a shell command"
actually does.

Deliberately excludes buy_plugin/delete_plugin/list_store_plugins/
list_purchased_plugins -- marketplace-only concerns, not part of the local
dev/test loop. Also excludes install_plugin: that handler is the
purchase-flow stub (it hands back a URL rather than installing anything --
see plugin_management.py's module docstring) -- generate_plugin_scaffold's
own install_generated_plugin is the real local-dev install/start path.
"""

from __future__ import annotations

import functools
from typing import Any

from nucore import NuCoreInterface
from utils import get_logger

from ..dispatch import ToolHandler
from ..handlers import plugin_management, shell
from .evidence_ledger import EvidenceLedger
from .handlers import device_detection, discovery, install, profile_authoring, scaffold, workspace

logger = get_logger(__name__)

TOOL_HANDLERS: dict[str, ToolHandler] = {
    "validate_profile": profile_authoring.validate_profile,
    "lookup_uom": profile_authoring.lookup_uom,
    "configure_plugin": plugin_management.configure_plugin,
    "list_installed_plugins": plugin_management.list_installed_plugins,
    "plugin_ops": plugin_management.plugin_ops,
    "get_plugin_capabilities": plugin_management.get_plugin_capabilities,
    "call_plugin": plugin_management.call_plugin,
    "run_shell_command": shell.run_shell_command,
    "detect_usb_device": device_detection.detect_usb_device,
}


def build_tool_handlers(
    *,
    ledger: EvidenceLedger,
    search_engine: str | None,
    search_engine_api_key: str | None,
    secret_values: list[str],
    plugin_output_root: str,
) -> dict[str, ToolHandler]:
    """Fresh per-connection handlers dict: the tools above, unchanged, plus
    the Phase 2 discovery tools and the Phase 3 workspace-discovery tools
    (design/developers/impl_plan.md), each pre-bound to this connection's own
    state via ``functools.partial`` so ``execute_tool``'s own signature
    never has to change. ``search_web`` is only added when both
    *search_engine* and *search_engine_api_key* are truthy -- "registered
    only when the key is configured" applies equally to either provider,
    not just one of them. ``list_generated_plugins``/``read_generated_plugin``
    are always added -- local-disk-only, nothing external to be missing."""
    handlers: dict[str, ToolHandler] = dict(TOOL_HANDLERS)
    handlers["search_store_plugins"] = functools.partial(discovery.search_store_plugins, ledger=ledger)
    handlers["search_github_plugins"] = functools.partial(
        discovery.search_github_plugins, ledger=ledger, secret_values=secret_values
    )
    handlers["fetch_reference"] = functools.partial(
        discovery.fetch_reference, ledger=ledger, secret_values=secret_values
    )
    if search_engine and search_engine_api_key:
        handlers["search_web"] = functools.partial(
            discovery.search_web,
            ledger=ledger,
            search_engine=search_engine,
            search_engine_api_key=search_engine_api_key,
            secret_values=secret_values,
        )
    handlers["list_generated_plugins"] = functools.partial(
        workspace.list_generated_plugins, plugin_output_root=plugin_output_root
    )
    handlers["read_generated_plugin"] = functools.partial(
        workspace.read_generated_plugin, plugin_output_root=plugin_output_root
    )
    handlers["generate_plugin_scaffold"] = functools.partial(
        scaffold.generate_plugin_scaffold,
        ledger=ledger,
        secret_values=secret_values,
        plugin_output_root=plugin_output_root,
    )
    handlers["install_generated_plugin"] = functools.partial(
        install.install_generated_plugin, plugin_output_root=plugin_output_root
    )
    return handlers


async def execute_tool(
    name: str, args: dict[str, Any], *, nucore_interface: NuCoreInterface, tool_handlers: dict[str, ToolHandler] | None = None
) -> Any:
    """Same contract as unified.dispatch.execute_tool -- never raises.
    *tool_handlers*, when given, is the per-connection dict ``build_tool_handlers``
    produced (so discovery tools see this connection's own ledger); omitting
    it falls back to the module-level ``TOOL_HANDLERS`` -- the pre-Phase-2
    default, which every existing call/test keeps using unchanged."""
    handlers = tool_handlers if tool_handlers is not None else TOOL_HANDLERS
    handler = handlers.get(name)
    if handler is None:
        return {"error": f"unknown tool '{name}'"}

    try:
        return await handler(nucore_interface, args)
    except Exception as exc:
        logger.error(f"plugin_authoring tool '{name}' raised: {exc}")
        return {"error": f"'{name}' failed: {exc}"}

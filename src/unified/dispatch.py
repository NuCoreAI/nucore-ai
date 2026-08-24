"""Tool name -> handler dispatch for the unified agentic loop."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from nucore import NuCoreInterface
from utils import get_logger

from .handlers import (
    command_control_status,
    diagnostics,
    group_scene_ops,
    node_ops,
    plan,
    plugin_management,
    preferences,
    routine_automation,
    routine_status_ops,
    shell,
    variable_ops,
)

logger = get_logger(__name__)

# Neither Plan nor Diagnostics has a blanket lock -- every tool in both is an
# ordinary, always-available tool (see handlers/plan.py, handlers/
# diagnostics.py). The only mutual exclusion either needs is a hardware fact,
# not a conversational one, so it's enforced where the hardware lives
# (IoXDiagnostics._begin_plm_op/_end_plm_op, shared by the four promoted
# diagnostic tools and Plan's pair_device via NuCoreInterface.begin_plm_op/
# end_plm_op) rather than here.
#
# This set means only "this handler needs session_id forwarded to find its
# own state" -- Plan's seven staged-ops tools need it to look up *this
# conversation's* staged changes; nothing here is about locking.
_SESSION_SCOPED_TOOLS = frozenset({
    "propose_scene", "propose_automation", "propose_variable",
    "review_plan", "revise_plan", "apply_plan", "discard_plan",
})

ToolHandler = Callable[[NuCoreInterface, dict[str, Any]], Awaitable[Any]]

TOOL_HANDLERS: dict[str, ToolHandler] = {
    "get_property": command_control_status.get_property,
    "send_command": command_control_status.send_command,
    "node_op": node_ops.node_op,
    "group_scene_op": group_scene_ops.group_scene_op,
    "get_group_detail": group_scene_ops.get_group_detail,
    "multi_device_scene": group_scene_ops.multi_device_scene,
    "routine_status_op": routine_status_ops.routine_status_op,
    "create_or_update_routine": routine_automation.create_or_update_routine,
    "get_device_detail": routine_automation.get_device_detail,
    "get_routine_detail": routine_automation.get_routine_detail,
    "variable_op": variable_ops.variable_op,
    "list_variables": variable_ops.list_variables,
    "list_store_plugins": plugin_management.list_store_plugins,
    "list_purchased_plugins": plugin_management.list_purchased_plugins,
    "list_installed_plugins": plugin_management.list_installed_plugins,
    "install_plugin": plugin_management.install_plugin,
    "buy_plugin": plugin_management.buy_plugin,
    "delete_plugin": plugin_management.delete_plugin,
    "get_plugin_capabilities": plugin_management.get_plugin_capabilities,
    "call_plugin": plugin_management.call_plugin,
    "get_full_system_config": diagnostics.get_full_system_config,
    "get_device_family": diagnostics.get_device_family,
    "get_dev_links_table": diagnostics.get_dev_links_table,
    "get_iox_links_table": diagnostics.get_iox_links_table,
    "compare_device_links": diagnostics.compare_device_links,
    "get_all_plm_links": diagnostics.get_all_plm_links,
    "quick_plm_sanity_check": diagnostics.quick_plm_sanity_check,
    "get_diagnostics_prompt": diagnostics.get_diagnostics_prompt,
    "get_plan_prompt": plan.get_plan_prompt,
    "create_folder": plan.create_folder,
    "pair_device": plan.pair_device,
    "propose_scene": plan.propose_scene,
    "propose_automation": plan.propose_automation,
    "propose_variable": plan.propose_variable,
    "review_plan": plan.review_plan,
    "revise_plan": plan.revise_plan,
    "apply_plan": plan.apply_plan,
    "discard_plan": plan.discard_plan,
    "list_preferences": preferences.list_preferences,
    "preference_op": preferences.preference_op,
    "run_shell_command": shell.run_shell_command,
}


async def execute_tool(
    name: str, args: dict[str, Any], *, nucore_interface: NuCoreInterface, session_id: str | None = None
) -> Any:
    """Look up and run *name*'s handler, returning a JSON-serializable
    result or ``{"error": ...}`` -- never raises, so one bad tool call can't
    take down the agentic loop.

    *session_id* identifies which conversation this call came from -- used
    only to key Plan's staged-ops state for the tools in
    ``_SESSION_SCOPED_TOOLS``; every other handler ignores it."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool '{name}'"}

    try:
        if name in _SESSION_SCOPED_TOOLS:
            return await handler(nucore_interface, args, session_id=session_id)
        return await handler(nucore_interface, args)
    except Exception as exc:
        logger.error(f"tool '{name}' raised: {exc}")
        return {"error": f"'{name}' failed: {exc}"}

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

# Tools still allowed through while a plan session is running -- everything
# else is refused (see execute_tool), for every session. Even these two are
# only let through for the session_id that started the active plan -- a real
# hub-level operation another conversation shouldn't be able to touch,
# restart, or interrupt.
_PLAN_EXEMPT_TOOLS = frozenset({"start_plan", "run_plan_step"})

# Diagnostics has no session at all any more -- run_diagnostic_step/
# get_diagnostics_prompt are ordinary, always-available tools, neither needs
# session_id forwarded. The 4 steps that actually touch the shared PLM
# connection (get_dev_links_table, compare_device_links, get_all_plm_links,
# quick_plm_sanity_check) enforce their own narrow, atomic mutual exclusion
# in IoXDiagnostics -- see _begin_plm_op/_end_plm_op -- independent of this
# dispatcher entirely.
_SESSION_SCOPED_TOOLS = _PLAN_EXEMPT_TOOLS

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
    "run_diagnostic_step": diagnostics.run_diagnostic_step,
    "get_diagnostics_prompt": diagnostics.get_diagnostics_prompt,
    "start_plan": plan.start_plan,
    "run_plan_step": plan.run_plan_step,
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
    only for the plan ownership check below; every other handler ignores it."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool '{name}'"}

    running_plan = plan.get_running_plan(nucore_interface)
    if running_plan is not None:
        owner = running_plan.get("session_id")
        if name not in _PLAN_EXEMPT_TOOLS or session_id != owner:
            return {
                "error": (
                    f"a plan session is currently in progress "
                    f"(started {running_plan['elapsed_s']}s ago) -- no other actions can be performed until it "
                    "concludes, times out, or is stopped. Ask the customer whether to stop it, then call "
                    "run_plan_step with step='stop', or wait."
                )
            }

    try:
        if name in _SESSION_SCOPED_TOOLS:
            return await handler(nucore_interface, args, session_id=session_id)
        return await handler(nucore_interface, args)
    except Exception as exc:
        logger.error(f"tool '{name}' raised: {exc}")
        return {"error": f"'{name}' failed: {exc}"}

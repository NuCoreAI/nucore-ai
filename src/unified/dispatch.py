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
    plugin_management,
    routine_automation,
    routine_status_ops,
    variable_ops,
)

logger = get_logger(__name__)

# Tools still allowed through while a diagnostic is running -- everything
# else is refused (see execute_tool). Not session-scoped: the lock lives on
# nucore_interface itself (one shared backend), so this blocks every
# session uniformly rather than needing per-session tracking.
_DIAGNOSTICS_EXEMPT_TOOLS = frozenset({"run_diagnostics", "list_diagnostics"})

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
    "list_diagnostics": diagnostics.list_diagnostics,
    "run_diagnostics": diagnostics.run_diagnostics,
}


async def execute_tool(name: str, args: dict[str, Any], *, nucore_interface: NuCoreInterface) -> Any:
    """Look up and run *name*'s handler, returning a JSON-serializable
    result or ``{"error": ...}`` -- never raises, so one bad tool call can't
    take down the agentic loop."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool '{name}'"}

    if name not in _DIAGNOSTICS_EXEMPT_TOOLS:
        running = nucore_interface.get_running_diagnostic()
        if running is not None:
            return {
                "error": (
                    f"a diagnostic ('{running['diagnostics']}') is currently running "
                    f"(started {running['elapsed_s']}s ago) -- no other actions can be performed until it "
                    "finishes, times out, or is stopped. Ask the customer whether to stop it, then call "
                    "run_diagnostics with the stop function's exact name from list_diagnostics, or wait."
                )
            }

    try:
        return await handler(nucore_interface, args)
    except Exception as exc:
        logger.error(f"tool '{name}' raised: {exc}")
        return {"error": f"'{name}' failed: {exc}"}

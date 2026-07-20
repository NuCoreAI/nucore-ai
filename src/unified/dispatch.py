"""Tool name -> handler dispatch for the unified agentic loop."""

from __future__ import annotations

from typing import Any, Awaitable, Callable

from nucore import NuCoreInterface
from utils import get_logger

from .handlers import (
    command_control_status,
    group_scene_ops,
    node_ops,
    routine_automation,
    routine_status_ops,
    variable_ops,
)

logger = get_logger(__name__)

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
}


async def execute_tool(name: str, args: dict[str, Any], *, nucore_interface: NuCoreInterface) -> Any:
    """Look up and run *name*'s handler, returning a JSON-serializable
    result or ``{"error": ...}`` -- never raises, so one bad tool call can't
    take down the agentic loop."""
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool '{name}'"}
    try:
        return await handler(nucore_interface, args)
    except Exception as exc:
        logger.error(f"tool '{name}' raised: {exc}")
        return {"error": f"'{name}' failed: {exc}"}

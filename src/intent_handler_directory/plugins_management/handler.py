from __future__ import annotations

import os, json
import importlib
from pathlib import Path
from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult


class PluginsManagementIntentHandler(BaseIntentHandler):
    """Intent handler for managing nucore plugins."""

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        framework_context=None,
        route_result=None,
    ) -> dict[str, str]:
        try:
            plugins = await self._get_plugins()
            if plugins:
                return {
                    "<<plugins_marketplace>>": f"```json\n{json.dumps(plugins, indent=2)}\n```\n"
                }
            return {
                "<<plugins_marketplace>>": "No plugins marketplace information available."
            }
        except Exception as exc:
            return {
                "<<plugins_marketplace>>": f"Error retrieving plugins marketplace information: {exc}"
            }


    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: dict = None,
        raw_response: IntentHandlerResult | None = None,
        tool_calls=None,
    ) -> IntentHandlerResult | None:
        response = raw_response
        if response is None:
            return None

        tools = tool_calls if tool_calls is not None else response.get_tool_calls()
        if tools:
            for tool in tools:
                if tool.name != "tool_extension_marketplace":
                    response.add_tool_result(tool_result=f"Unknown tool called: {tool.name}")
                    continue
                response.add_tool_result(tool_result=self._process_marketplace_tool(tool.args))

        response.set_route_result(route_result=route_result)
        return response
    
    def __list_extensions(self, extensions: list[dict[str, Any]]) -> str:
        if not extensions:
            return "No NuCore.ai extensions found."
        out = "" 
        for ext in extensions:
            out += f"**Extension Id**: {ext.get('id', 'N/A')}\n\n"
            out += f"**Name**: {ext.get('name', 'N/A')}\n\n"
            out += f"```json\n{json.dumps(ext)}\n```\n"
        return out

    def _process_marketplace_tool(self, args: Any) -> dict[str, Any] | list[dict[str, Any]] | str:
        payload = args
        if isinstance(payload, list):
            if not payload:
                return "Invalid tool call: empty arguments"
            if isinstance(payload[0], dict):
                payload = payload[0]

        if not isinstance(payload, dict):
            return "Invalid tool call: arguments must be an object"

        action = str(payload.get("action", "")).strip().lower()
        manager = self._get_marketplace_manager()

        try:
            extension_id = str(payload.get("extension_id", "")).strip()
            if action in {"install", "update", "uninstall"} and not extension_id:
                return "Invalid tool call: extension_id is required"

            if action == "install":
                return manager.install_extension(extension_id, target_ref=payload.get("target_ref"))

            if action == "update":
                return manager.update_extension(extension_id, target_ref=payload.get("target_ref"))

            if action == "uninstall":
                return manager.uninstall_extension(extension_id)

            if action == "list_installed":
                return self.__list_extensions(manager.list_installed_extensions())

            return f"Unsupported marketplace action: {action}"
        except Exception as exc:
            return f"Error processing marketplace action '{action}': {exc}"

    async def _get_plugins(self) -> Any:
        out = []
        if self.nucore_interface is not None:
            try:
                plugins = await self.nucore_interface.get_active_plugins()
                plugins = plugins.get("data", [])
                if not plugins or not isinstance(plugins, list) or len(plugins) == 0:
                    return None 
                for plugin in plugins:
                    out.append({
                        "id": plugin.get("nsid"),
                        "desc": plugin.get("desc", "No description available")
                    })
                return out if out else None
            except Exception as exc:
                return None
        return None
        
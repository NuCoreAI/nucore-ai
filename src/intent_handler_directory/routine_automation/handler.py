from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult
from utils import get_logger
from utils import _get_candidate_devices_from_routines, _get_full_routines_from_candidates

logger = get_logger(__name__)

def debug(msg: str) -> None:
    """Log a debug-level message prefixed with ``[PROFILE FORMAT ERROR]``."""
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")


class RoutineAutomationIntentHandler(BaseIntentHandler):
    """Intent handler for creating and managing automation routines.

    The LLM is expected to call ``tool_routine_automation`` with a list of
    routine definitions.  Each definition is forwarded to
    :meth:`~nucore.NuCoreInterface.create_automation_routine` on the NuCore
    backend.  Results for every routine in the batch are accumulated and
    attached to the response as tool results.

    This handler has no prompt placeholders — ``get_prompt_runtime_replacements``
    returns replacements derived from route context and runtime state.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        framework_context=None,
        route_result=None,
    ) -> dict:
        """Return an empty replacement dict (no dynamic placeholders needed).

        Args:
            query:              The user query (unused).
            framework_context:  Unused for this handler.
            route_result:       Unused for this handler.

        Returns:
            Empty dict — the static prompt requires no runtime substitution.
        """
        policy_modules = self._load_prompt_modules()
        location_information = await self.nucore_interface.get_timespecs() if self.nucore_interface else None 
        temporal_resolution = self.get_route_context_value(route_result, "temporal_resolution", None)
        temporal_resolution_block = (
            ""
            if not temporal_resolution
            else (
                "---\n"
                "# TEMPORAL RESOLUTION\n"
                "Use this resolved holiday window as trusted schedule input.\n"
                f"```json\n{json.dumps(temporal_resolution, indent=2)}\n```"
            )
        )
        
        if route_result and route_result.route_context:
            candidate_devices = self.get_route_context_value(route_result, "candidate_devices", [])
            if not candidate_devices:
                candidate_devices = []
            
            candidate_routines = self.get_route_context_value(route_result, "candidate_routines", [])
            if not candidate_routines:
                candidate_rags = self._get_rags_from_candidates(candidate_devices)
                return {
                    "<<runtime_device_structure>>": "" if not candidate_rags else candidate_rags,
                    "<<routine_automation_policy_modules>>": policy_modules,
                    "<<location_information>>": "Get from the user" if not location_information else f"```json\n{json.dumps(location_information, indent=2)}\n```",  
                    "<<temporal_resolution_context>>": temporal_resolution_block,
                }
            

            # we are editing. The first thing to do is to get the candidate routines for editing
            # to the candidate devices if any exist. This is because the router may have filtered out devices that are actually part of the routine.
            candidate_routines = await _get_full_routines_from_candidates(self, candidate_routines)
            extra_devices = _get_candidate_devices_from_routines(candidate_routines)
            if extra_devices:
                candidate_devices.extend(extra_devices)
            candidate_rags = self._get_rags_from_candidates(candidate_devices)
            return {
                        "<<runtime_device_structure>>": "" if not candidate_rags else candidate_rags,
                        "<<existing_routines>>": "" if not candidate_routines else f"```json\n{json.dumps(candidate_routines, indent=2)}\n```",
                        "<<routine_automation_policy_modules>>": policy_modules,
                        "<<location_information>>": "Get from the user" if not location_information else f"```json\n{json.dumps(location_information, indent=2)}\n```",  
                        "<<temporal_resolution_context>>": temporal_resolution_block,
                    }

        return {
            "<<runtime_device_structure>>": "",
            "<<routine_automation_policy_modules>>": policy_modules,
            "<<location_information>>": "Get from the user" if not location_information else f"```json\n{json.dumps(location_information, indent=2)}\n```",
            "<<temporal_resolution_context>>": temporal_resolution_block,
        }

    def _load_prompt_modules(self) -> str:
        """Load optional intent-local prompt policy modules from prompt_modules/*.md."""
        modules_dir = Path(self.directory) / "prompt_modules"
        if not modules_dir.exists() or not modules_dir.is_dir():
            return ""

        sections: list[str] = []
        for module_file in sorted(modules_dir.glob("*.md")):
            try:
                content = module_file.read_text(encoding="utf-8").strip()
            except Exception as exc:
                debug(f"Failed to read prompt module '{module_file.name}': {exc}")
                continue
            if not content:
                continue
            sections.append(f"---\n# MODULE: {module_file.stem}\n{content}")

        return "\n\n".join(sections).strip()
    

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: dict = None,
        raw_response: IntentHandlerResult | None = None,
        tool_calls=None,
    ):
        """Call the LLM and dispatch any ``tool_routine_automation`` tool calls.

        After receiving the LLM response, iterates over all returned tool calls
        and routes each to :meth:`_process_routine_automation`.  Tool results
        are accumulated on the response object so the runtime or downstream
        handlers can inspect them.

        Args:
            query:               The user query string.
            route_result:        Routing metadata forwarded to message assembly
                                 and stamped on the result (set twice to ensure
                                 it is present both before and after tool
                                 dispatch).
            framework_context:   Optional runtime context dictionary from eisyui showing which page/url we are on.

        Returns:
            :class:`~intent_handler.IntentHandlerResult` with tool results
            attached and route metadata set.
        """
        
        response = raw_response
        response.set_route_result(route_result=route_result)

        # Dispatch each tool call and collect the backend results.
        tools = tool_calls if tool_calls is not None else response.get_tool_calls()
        if tools:
            for tool in tools:
                if tool.name == "tool_routine_automation":
                    result = await self._process_routine_automation(tool)
                else:
                    result = f"Unknown tool called: {tool.name}"
                response.add_tool_result(tool_result=result)

        response.set_route_result(route_result=route_result)
        return response

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def _process_routine_automation(self, tool) -> list | str:
        """Submit a batch of automation routine definitions to the NuCore backend.

        Iterates over the list of routine dicts in ``tool.args`` and calls
        :meth:`~nucore.NuCoreInterface.create_automation_routine` for each
        one, collecting the individual results into a list.

        Args:
            tool: :class:`~intent_handler.adapters.ToolCall` whose ``args`` is
                  a list of routine definition dicts.

        Returns:
            List of results (one per routine) from the backend, or an error
            string when the call cannot be made.
        """
        if tool is None or tool.args is None:
            return "Invalid tool call: missing arguments"
        if self.nucore_interface is None:
            return "NuCore interface/backend not available"
        try:
            result = []
            for routine in tool.args:
                id = routine.get('id', None)
                if id is None or id == "":
                    result.append(await self.nucore_interface.create_automation_routine(routine))
                else:
                    #this is an update
                    result.append(await self.nucore_interface.update_routine(routine))
            return result
        except Exception as e:
            return f"Error processing routine automation tool: {str(e)}"


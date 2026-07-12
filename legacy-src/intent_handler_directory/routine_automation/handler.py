from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult
from utils import get_logger
from utils import _get_candidate_devices_from_routines, _get_full_routines_from_candidates

# handler.py is loaded standalone via importlib.util.spec_from_file_location
# (see loader.py), so it is not part of the intent_handler_directory package
# at import time — a bare `from routine_compiler import ...` would not
# resolve. Import via the fully-qualified package path instead, mirroring
# the pattern extension_marketplace_management/handler.py uses for its own
# sibling module.
from intent_handler_directory.routine_automation import compile_routine_source, RoutineCompileError

logger = get_logger(__name__)


class RoutineAutomationPythonIntentHandler(BaseIntentHandler):
    """Experimental intent handler for creating/updating automation routines.

    Identical in shape to :mod:`routine_automation` (same RAG-based device
    candidate injection, same existing-routine/temporal-resolution context),
    except the LLM describes routine logic as a small Python-like snippet
    (``tool_routine_automation_python``'s ``code`` field) instead of nested
    JSON. :func:`routine_compiler.compile_routine_source` translates that
    snippet into the exact routine dict the original JSON-based handler
    produces, so the NuCore backend call below is unchanged.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        framework_context=None,
        route_result=None,
    ) -> dict:
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
                    "<<existing_routines>>": "",
                    "<<routine_automation_policy_modules>>": policy_modules,
                    "<<location_information>>": "Get from the user" if not location_information else f"```json\n{json.dumps(location_information, indent=2)}\n```",
                    "<<temporal_resolution_context>>": temporal_resolution_block,
                }

            # We are editing. Resolve candidate routines to their full logic first,
            # since the router may have filtered out devices that are actually
            # part of the existing routine's if/then/else.
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
            "<<existing_routines>>": "",
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
                logger.debug(f"Failed to read prompt module '{module_file.name}': {exc}")
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
        """Call the LLM and dispatch any ``tool_routine_automation_python`` tool calls.

        Compiles each routine's ``code`` field with the AST-based compiler,
        then forwards the resulting routine dict to the NuCore backend exactly
        like the JSON-based handler does. Compile failures are returned as
        tool results (visible as the routine's outcome) rather than raised,
        matching the error-handling shape of the rest of the runtime.
        """
        response = raw_response
        response.set_route_result(route_result=route_result)

        tools = tool_calls if tool_calls is not None else response.get_tool_calls()
        if tools:
            for tool in tools:
                if tool.name == "tool_routine_automation_python":
                    result = await self._process_routine_automation_python(tool)
                else:
                    result = f"Unknown tool called: {tool.name}"
                response.add_tool_result(tool_result=result)

        response.set_route_result(route_result=route_result)
        return response

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def _process_routine_automation_python(self, tool) -> list | str:
        """Compile and submit a batch of routine DSL snippets to the NuCore backend.

        Args:
            tool: :class:`~intent_handler.adapters.ToolCall` whose ``args`` is
                  a list of ``{"name","id","comment","code"}`` dicts.

        Returns:
            List of per-routine results (backend result, or a compile-error
            string for routines that failed to translate).
        """
        if tool is None or tool.args is None:
            return "Invalid tool call: missing arguments"
        if self.nucore_interface is None:
            return "NuCore interface/backend not available"

        result: list[Any] = []
        for routine_spec in tool.args:
            name = routine_spec.get("name")
            routine_id = routine_spec.get("id")
            comment = routine_spec.get("comment", "")
            code = routine_spec.get("code", "")

            try:
                compiled = compile_routine_source(
                    name=name,
                    routine_id=routine_id,
                    comment=comment,
                    source=code,
                )
            except RoutineCompileError as exc:
                logger.debug(f"Routine '{name}' failed to compile: {exc}", extra={"code": code})
                result.append(f"Routine '{name}' failed to compile: {exc}")
                continue

            try:
                if routine_id is None or routine_id == "":
                    result.append(await self.nucore_interface.create_automation_routine(compiled))
                else:
                    result.append(await self.nucore_interface.update_routine(compiled))
            except Exception as e:
                result.append(f"Error processing routine automation tool: {str(e)}")

        return result

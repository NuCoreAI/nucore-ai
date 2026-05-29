from __future__ import annotations
from os import replace
from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult
from rag import RAGData
from utils import get_logger

logger = get_logger(__name__)

class CommandControlStatusIntentHandler(BaseIntentHandler):
    """Intent handler for issuing device commands and querying real-time device status.

    Supports two LLM-callable tools:

    * ``tool_command_control``  — sends one or more commands to the NuCore
      backend via :meth:`_process_command_control_tool`.
    * ``tool_real_time_status`` — fetches live property values for a list of
      ``(device, property)`` pairs via :meth:`_process_real_time_status_tool`.

    Candidate context from routing is injected into the prompt under the
    ``<<runtime_device_structure>>`` placeholder so the LLM has device
    context when deciding which commands to issue.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        framework_context=None,
        route_result=None,
    ):
        """Build the ``<<runtime_device_structure>>`` prompt placeholder value.

        Args:
            query:               The user query (unused here; reserved for
                                 subclass overrides).
            framework_context:   Unused; present for interface compatibility.
            route_result:        Unused; present for interface compatibility.

        Returns:
            Dict mapping ``"<<runtime_device_structure>>"`` to the assembled
            context string.
        """
        if route_result and route_result.route_context:
            # If the router provided candidate devices in the route context, use those directly.
            candidate_rags = self._get_rags_from_candidates(route_result.route_context.get("candidate_devices", []))
            return {"<<runtime_device_structure>>": "" if not candidate_rags else candidate_rags}


        return {"<<runtime_device_structure>>": "Not available!"}

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: str = None,
        raw_response: IntentHandlerResult | None = None,
        tool_calls=None,
    ):
        """Execute the intent: call the LLM and dispatch any returned tool calls.

        After receiving the LLM response, iterates over all tool calls and
        routes each to the appropriate private helper.  Tool results are
        accumulated on the response object so downstream handlers or the
        runtime can read them back.

        Args:
            query:               The user query string.
            route_result:        Routing metadata forwarded to message assembly.
            framework_context:   Optional extra context string.

        Returns:
            :class:`~intent_handler.IntentHandlerResult` with tool results
            attached, or ``None`` if the LLM returns an empty response.
        """
        response = raw_response
        if not response:
            return None

        # Dispatch each tool call to the appropriate handler and accumulate results.
        tools = tool_calls if tool_calls is not None else response.get_tool_calls()
        if tools:
            for tool in tools:
                if tool.name == "tool_command_control":
                    result = await self._process_command_control_tool(tool)
                elif tool.name == "tool_real_time_status":
                    result = await self._process_real_time_status_tool(tool)
                else:
                    result = f"Unknown tool called: {tool.name}"
                response.add_tool_result(tool_result=result)

        response.set_route_result(route_result=route_result)
        return response

    async def get_tool_result_prompt(self)->str:
        return "<<nucore_definitions>>\n\n<<nucore_common_rules>>\n\n---\n# DEVICE STRUCTURE\n\n<<runtime_device_structure>> "

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    async def _process_command_control_tool(self, tool) -> Any:
        """Send device commands to the NuCore backend.

        Args:
            tool: :class:`~intent_handler.adapters.ToolCall` whose ``args``
                  contain the command payload expected by
                  :meth:`~nucore.NuCoreInterface.send_commands`.

        Returns:
            The result returned by ``nucore_interface.send_commands``, or an
            error string when the call cannot be made.
        """
        if tool is None or tool.args is None:
            return "Invalid tool call: missing arguments"
        if self.nucore_interface is None:
            return "NuCore interface/backend not available"
        try:
            return await self.nucore_interface.send_commands(tool.args)
        except Exception as e:
            return f"Error processing command control tool: {str(e)}"

    async def _process_real_time_status_tool(self, tool) -> list[str] | None:
        """Fetch live property values for a list of device/property pairs.

        ``tool.args`` is expected to be a list of dicts, each with:

        * ``device`` or ``device_id``   — the device identifier.
        * ``property`` or ``property_id`` — the property key to read (optional;
          when omitted the device entry is skipped with a warning).

        Handles both a flat list and a singly-nested list (``[[...]]``) that
        some providers emit when wrapping array arguments.

        Args:
            tool: :class:`~intent_handler.adapters.ToolCall` whose ``args``
                  hold the property query list.

        Returns:
            List of human-readable ``"device_name: value"`` strings for each
            resolved property, or ``None`` when the query list is empty.
        """
        if tool is None or tool.args is None:
            return "Invalid tool call: missing arguments"
        if self.nucore_interface is None:
            return "NuCore interface/backend not available"

        prop_query = tool.args
        if not prop_query or len(prop_query) == 0:
            logger.warning("No property query provided")
            return None

        # Some providers wrap the array in an extra list layer; unwrap if needed.
        try:
            if isinstance(prop_query[0], list):
                prop_query = prop_query[0]
        except Exception:
            pass

        texts: list[str] = []
        for property in prop_query:
            device_id = property.get('device') or property.get('device_id')
            if not device_id:
                logger.warning("No device ID provided for property query", extra={"property": property})
                continue

            properties = await self.nucore_interface.get_properties(device_id)
            if not properties:
                logger.warning("No properties found for device", extra={"device_id": device_id})
                continue

            prop_id = property.get('property') or property.get('property_id')
            device_name = self.nucore_interface.get_device_name(device_id) or device_id

            if prop_id:
                prop = properties.get(prop_id)
                if prop:
                    # Prefer the formatted display value; fall back to raw value.
                    texts.append(f"{device_name}: {prop.name if prop.name else prop.id} = {prop.formatted if prop.formatted else prop.value}")
                else:
                    texts.append(f"Property {prop_id} not found for device {device_name}")
                    logger.warning("Property not found for device", extra={"property_id": prop_id, "device_name": device_name})
            else:
                logger.warning("No property ID provided for device", extra={"device_name": device_name})

        return texts
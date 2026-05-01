from __future__ import annotations

from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult


class RoutineAutomationIntentHandler(BaseIntentHandler):
    """Intent handler for creating and managing automation routines.

    The LLM is expected to call ``tool_routine_automation`` with a list of
    routine definitions.  Each definition is forwarded to
    :meth:`~nucore.NuCoreInterface.create_automation_routine` on the NuCore
    backend.  Results for every routine in the batch are accumulated and
    attached to the response as tool results.

    This handler has no prompt placeholders — ``get_prompt_runtime_replacements``
    returns an empty dict, relying entirely on the static prompt and any
    ``dependency_outputs`` passed through ``build_messages``.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        dependency_outputs: IntentHandlerResult | None = None,
        framework_context=None,
        route_result=None,
    ) -> dict:
        """Return an empty replacement dict (no dynamic placeholders needed).

        Args:
            query:              The user query (unused).
            dependency_outputs: Unused for this handler.
            framework_context:  Unused for this handler.
            route_result:       Unused for this handler.

        Returns:
            Empty dict — the static prompt requires no runtime substitution.
        """
        return {}

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: str = None,
        dependency_outputs: IntentHandlerResult | str | dict[str, Any] | None = None,
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
            framework_context:   Optional extra context string.
            dependency_outputs:  Outputs from preceding intents in the chain.

        Returns:
            :class:`~intent_handler.IntentHandlerResult` with tool results
            attached and route metadata set.
        """
        messages = await self.build_messages(
            query,
            dependency_outputs=dependency_outputs,
            framework_context=framework_context,
            route_result=route_result,
        )
        response = await self.call_llm(messages=messages)
        response.set_route_result(route_result=route_result)

        # Dispatch each tool call and collect the backend results.
        tools = response.get_tool_calls()
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
                result.append(await self.nucore_interface.create_automation_routine(routine))
            return result
        except Exception as e:
            return f"Error processing routine automation tool: {str(e)}"
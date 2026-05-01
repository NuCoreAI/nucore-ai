from __future__ import annotations

from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult


class GeneralHelpIntentHandler(BaseIntentHandler):
    """Catch-all intent handler for general user questions about the system.

    Provides the LLM with a high-level device summary (``<<device_database>>``)
    so it can answer questions about what devices are present without performing
    any command or filtering.  Does not call any tools — the LLM responds in
    plain text.

    If the router itself failed to produce valid JSON (indicated by a
    ``"Router returned non-JSON text:"`` note on ``route_result``), the handler
    bails out early and returns ``None`` rather than propagating a confusing
    response.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        dependency_outputs: IntentHandlerResult | None = None,
        framework_context=None,
        route_result=None,
    ) -> dict:
        """Refresh the device structure and supply the ``<<device_database>>`` placeholder.

        Forces a refresh of the NuCore device structure so the LLM always
        receives an up-to-date device summary.

        Args:
            query:              The user query (unused; reserved for subclass
                                overrides).
            dependency_outputs: Unused for this handler.
            framework_context:  Unused for this handler.
            route_result:       Unused for this handler.

        Returns:
            Dict with ``"<<device_database>>"`` mapped to the summary RAGs
            object (or ``None`` when unavailable).
        """
        # Ensure the device structure is current before injecting it into the prompt.
        await self.nucore_interface._refresh_device_structure()
        return {
            "<<device_database>>": self.nucore_interface.summary_rags
        }

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: str = None,
        dependency_outputs: IntentHandlerResult | str | dict[str, Any] | None = None,
    ):
        """Answer a general help query using a plain LLM call (no tool use).

        Args:
            query:               The user query string.
            route_result:        Routing metadata; checked for router-failure
                                 notes before proceeding.
            framework_context:   Optional extra context string.
            dependency_outputs:  Unused for this handler.

        Returns:
            :class:`~intent_handler.IntentHandlerResult` with the LLM's text
            response, or ``None`` if the router itself produced an invalid
            (non-JSON) response.
        """
        # Abort early when the router failed to parse its own output — forwarding
        # a broken route to the LLM would produce a confusing or empty response.
        if route_result and route_result.notes and "Router returned non-JSON text:" in route_result.notes:
            return None

        messages = await self.build_messages(
            query,
            framework_context=framework_context,
            route_result=route_result,
        )
        response = await self.call_llm(messages=messages)
        response.set_route_result(route_result=route_result)
        return response
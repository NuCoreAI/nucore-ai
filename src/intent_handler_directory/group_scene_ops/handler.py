from __future__ import annotations

from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult
from rag import RAGData


class GroupSceneOperationsIntentHandler(BaseIntentHandler):
    """Intent handler for group and scene activation/management operations.

    Injects filtered device context from routing candidates into the
    ``<<runtime_device_structure>>`` prompt placeholder, then asks the LLM
    to produce the appropriate group/scene commands. The LLM response is
    returned as-is (plain text or tool calls) without any post-processing.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        framework_context=None,
        route_result=None,
    ) -> dict[str, str]:
        """Assemble the ``<<runtime_device_structure>>`` placeholder.

        Args:
            query:               The user query (unused; reserved for subclass
                                 overrides).
            framework_context:   Unused; present for interface compatibility.
            route_result:        Unused; present for interface compatibility.

        Returns:
            Dict mapping ``"<<runtime_device_structure>>"`` to the assembled
            device context string.
        """
        if route_result and route_result.route_context:
            # Pull latest candidate devices from accumulated multi-step contexts.
            candidate_devices = self.get_route_context_value(route_result, "candidate_devices", [])
            candidate_rags = self._get_rags_from_candidates(candidate_devices)
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
        """Call the LLM with device context and return its group/scene response.

        Args:
            query:               The user query string.
            route_result:        Routing metadata forwarded to message assembly
                                 and stamped on the result.
            framework_context:   Optional extra context string.

        Returns:
            :class:`~intent_handler.IntentHandlerResult` with the LLM response
            and route metadata attached.
        """
        response = raw_response
        response.set_route_result(route_result=route_result)
        return response


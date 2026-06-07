from __future__ import annotations

import json
from json import tool
from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult
from utils import get_logger
from utils import _get_routine_summary_from_candidates 


logger = get_logger(__name__)


def debug(msg: str) -> None:
    """Log a debug-level message prefixed with ``[PROFILE FORMAT ERROR]``."""
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")


class RoutineStatusOpsIntentHandler(BaseIntentHandler):
    """Intent handler for enabling, disabling, or toggling automation routines.

    Uses route-provided routine context to build prompt placeholders so the
    LLM knows which routines were matched.

    The LLM returns a list of ``{id, operation}`` dicts.  Each entry is
    dispatched to :meth:`~nucore.NuCoreInterface.routine_ops` on the NuCore
    backend.  The collected results become ``response.output``.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        framework_context=None,
        route_result=None,
    ) -> dict[str, str]:
        """Build the ``nucore_routines_runtime`` prompt placeholder.

        Builds a fenced JSON block so the LLM can reference matched routines by ID.

        Args:
            query:               The user query (unused; reserved for subclass
                                 overrides).
            framework_context:   Unused; present for interface compatibility.
            route_result:        Unused; present for interface compatibility.

        Returns:
            Dict with ``"nucore_routines_runtime"`` mapped to a fenced JSON
            block, or a ``"No routine runtime information available."`` fallback.
        """
        if route_result and route_result.route_context:
            # Pull latest candidate routines from accumulated multi-step contexts.
            candidate_routine_ids = self.get_route_context_value(route_result, "candidate_routines", [])
            candidate_routines = await _get_routine_summary_from_candidates(self, candidate_routine_ids)
            if candidate_routines:
                return {"<<nucore_routines_runtime>>": f"```json\n{json.dumps(candidate_routines, indent=2)}\n```"}
            else:
                return {"<<nucore_routines_runtime>>": f"**No Runtime Information Available**\n\n**ASK FOR CLARIFICATION BASED ON**: \n\n```json\n{json.dumps(self.nucore_interface.condensed_routines)}\n```"}

        return {
            "<<nucore_routines_runtime>>": "No routine runtime information available."
        }

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: dict = None,
        raw_response: IntentHandlerResult | None = None,
        tool_calls=None,
    ):
        """Execute routine status operations returned by the LLM.

        For each ``{id, operation}`` entry in the LLM tool-call args, calls
        :meth:`~nucore.NuCoreInterface.routine_ops` and accumulates non-``None``
        results.  The ``response.output`` is set to the result list, or to a
        descriptive string when no valid operations are found.

        Args:
            query:               The user query string.
            route_result:        Routing metadata stamped on the result.
            framework_context:   Optional runtime context dictionary from eisyui showing which page/url we are on.

        Returns:
            :class:`~intent_handler.IntentHandlerResult` whose ``output`` is a
            list of backend operation results, or a "no operations found" string.
        """
        response = raw_response

        tool_calls = tool_calls if tool_calls is not None else response.get_tool_calls()
        out: list = []

        if tool_calls:
            for tool_call in tool_calls:
                # Each tool call's args is a list of {id, operation} dicts.
                operations = tool_call.args
                if not operations:
                    continue
                for tool in operations:
                    routine_id = tool.get("id")
                    operation = tool.get("operation")
                    if routine_id is None or operation is None:
                        debug(f"Tool call missing 'id' or 'operation' field: {tool}")
                        continue
                    rc = await self.nucore_interface.routine_ops(routine_id=routine_id, operation=operation)
                    response.add_tool_result(rc if rc is not None else f"Operation '{operation}' on routine '{routine_id}' failed.")
        else:
            debug("No tool calls found in the response.")

        response.set_route_result(route_result=route_result)
        return response


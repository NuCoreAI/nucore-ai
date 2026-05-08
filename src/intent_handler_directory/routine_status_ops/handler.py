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

    Expects output from a preceding ``routine_filter`` intent as
    ``dependency_outputs``; that output is serialised as a JSON block and
    injected into the prompt under the ``nucore_routines_runtime`` placeholder
    so the LLM knows which routines were matched.

    The LLM returns a list of ``{id, operation}`` dicts.  Each entry is
    dispatched to :meth:`~nucore.NuCoreInterface.routine_ops` on the NuCore
    backend.  The collected results become ``response.output``.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        dependency_outputs: IntentHandlerResult | None = None,
        framework_context=None,
        route_result=None,
    ) -> dict[str, str]:
        """Build the ``nucore_routines_runtime`` prompt placeholder.

        Extracts the ``routine_filter`` dependency output (or falls back to the
        whole ``dependency_outputs`` dict) and serialises it as a fenced JSON
        block so the LLM can reference the matched routines by ID.

        Args:
            query:               The user query (unused; reserved for subclass
                                 overrides).
            dependency_outputs:  Dict of ``intent_name → IntentHandlerResult``;
                                 the ``"routine_filter"`` key is preferred.
            framework_context:   Unused; present for interface compatibility.
            route_result:        Unused; present for interface compatibility.

        Returns:
            Dict with ``"nucore_routines_runtime"`` mapped to a fenced JSON
            block, or a ``"No routine runtime information available."`` fallback.
        """
        if route_result and route_result.route_context:
            # If the router provided candidate devices in the route context, use those directly.
            candidate_routines = await _get_routine_summary_from_candidates(self, route_result.route_context.get("candidate_routines", []))
            return {"<<nucore_routines_runtime>>": "" if not candidate_routines else f"```json\n{json.dumps(candidate_routines, indent=2)}\n```"}

        return {
            "<<nucore_routines_runtime>>": "No routine runtime information available."
        }

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: str = None,
        dependency_outputs: IntentHandlerResult | str | dict[str, Any] | None = None,
    ):
        """Execute routine status operations returned by the LLM.

        For each ``{id, operation}`` entry in the LLM tool-call args, calls
        :meth:`~nucore.NuCoreInterface.routine_ops` and accumulates non-``None``
        results.  The ``response.output`` is set to the result list, or to a
        descriptive string when no valid operations are found.

        Args:
            query:               The user query string.
            route_result:        Routing metadata stamped on the result.
            framework_context:   Optional extra context string.
            dependency_outputs:  Outputs from preceding intents; passed to
                                 :meth:`build_messages` and
                                 :meth:`get_prompt_runtime_replacements`.

        Returns:
            :class:`~intent_handler.IntentHandlerResult` whose ``output`` is a
            list of backend operation results, or a "no operations found" string.
        """
        messages = await self.build_messages(
            query,
            framework_context=framework_context,
            route_result=route_result,
            dependency_outputs=dependency_outputs,
        )
        response = await self.call_llm(messages=messages)

        tool_calls = response.get_tool_calls()
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


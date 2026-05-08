from __future__ import annotations

import json
from json import tool
from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult
from utils import get_logger


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
            candidate_routines = await self._get_routine_summary_from_candidates(route_result.route_context.get("candidate_routines", []))
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

   # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_routine_summary_from_candidates(self, candidates) -> list[dict[str, Any]]:
        """Fetch routine summaries for candidates that meet the score threshold.

        Iterates the candidate list in ``tool.args``, discards entries below
        the configured threshold, then calls
        :meth:`~nucore.NuCoreInterface.get_routine_summary` for each passing
        candidate and flattens the results into a single list.

        Args:
            candidates: List of dicts with ``routine_id`` and ``score`` keys.

        Returns:
            Flat list of routine summary dicts for all candidates that passed
            the threshold.  Empty when no candidates qualify.
        """
        score_threshold = self.config.get("threshold", 0.80)

        out: list[dict[str, Any]] = []
        for r in candidates:
            if float(r.get('score', 0)) >= score_threshold:
                try:
                    routine = await self.nucore_interface.get_routine_summary(r['routine_id'])
                    if not routine:
                        debug("Received None routine summary from Nucore interface.")
                        continue
                    if isinstance(routine, list):
                        routine= routine[0] if routine else None
                        if not routine:
                            debug("Received empty list routine summary from Nucore interface.")
                            continue
                    # Enrich each summary dict with the full routine logic.
                    if 'id' not in routine:
                        debug(f"Routine summary missing 'id' field: {routine}")
                        continue
                    else:
                        routine_id = self._convert_routine_id_to_int(routine['id'])
                        if routine_id is None:
                            debug(f"Failed to convert routine ID {routine['id']} to int, skipping enrichment with full routine logic.")
                            continue
                        routine['id'] = routine_id
                        full_routine = self.nucore_interface.all_routines.get(routine_id)
                        if full_routine is None:
                            debug(f"No full routine found for routine ID: {routine_id}")
                        else:
                            # Attach the complete routine trigger/action logic for downstream use.
                            routine['routine_logic'] = self._replace_device_id_with_name(full_routine)
                    out.append(routine)
                except Exception:
                    pass
        return out

    def _replace_device_id_with_name(self, full_routine: dict[str, Any]) -> dict[str, Any]:
        """
        Scans the ``if``, ``then``, and ``else`` sections of the routine for
        ``"device"`` fields, resolves each raw address to its display name via
        :meth:`get_device_name`, and returns the deduplicated list.

        Args:
            full_routine: Full routine dict with optional ``if``/``then``/``else``
                          section lists.

        Returns:
            List of device display name strings (may be empty).
        """
        if full_routine is None:
            return []

        #first check the if section:        
        if_section: list[dict] = full_routine.get("if", [])
        then_section: list[dict] = full_routine.get("then", [])
        else_section: list[dict] = full_routine.get("else", [])
        device_id_list = []
        for condition in if_section:
            if "device" in condition:
                device = condition.get("device", None)
                if device:                    
                    device_name = self.nucore_interface.get_device_name(device)
                    condition["device"] = device_name if device_name else device
        
        for action in then_section:
            if "device" in action:
                device = action.get("device", None)
                if device:
                    device_name = self.nucore_interface.get_device_name(device)
                    action["device"] = device_name if device_name else device
   
        for action in else_section:
            if "device" in action:
                device = action.get("device", None)
                if device:
                    device_name = self.nucore_interface.get_device_name(device)
                    action["device"] = device_name if device_name else device

        return full_routine 

    def _convert_routine_id_to_int(self, routine_id: Any) -> int | None:
        """Convert a routine ID to a Python ``int``, accepting hex strings.

        The NuCore backend may return routine IDs as either plain integers or
        hexadecimal strings (e.g. ``"0x1a2b"``).  Both forms are normalised to
        ``int`` so they can be used as keys in ``all_routines``.

        Args:
            routine_id: The raw routine ID value from the LLM tool call.

        Returns:
            Integer routine ID, or ``None`` when conversion fails.
        """
        if isinstance(routine_id, int):
            return routine_id
        if isinstance(routine_id, str):
            try:
                # base-16 parsing handles both "0x…" prefixed and bare hex strings.
                return int(routine_id, 16)
            except ValueError:
                debug(f"Failed to convert routine ID {routine_id} to int using both decimal and hex parsing.")
                return None

        debug(f"Routine ID {routine_id} is neither int nor str, cannot convert to int.")
        return None

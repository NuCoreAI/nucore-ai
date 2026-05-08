from __future__ import annotations

from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult
from utils import get_logger


logger = get_logger(__name__)


def debug(msg: str) -> None:
    """Log a debug-level message prefixed with ``[PROFILE FORMAT ERROR]``."""
    logger.debug(f"[PROFILE FORMAT ERROR] {msg}")


class RoutineFilterIntentHandler(BaseIntentHandler):
    """Intent handler that filters the full routines database to a relevant subset.

    The LLM is given a condensed JSON snapshot of all routines
    (``<<routines_database>>``) and returns a ``tool_routine_filter`` call
    containing candidate routine IDs scored by relevance.  Only candidates at
    or above the configured ``threshold`` (default ``0.80``) are kept.

    For each matched candidate the handler:

    1. Fetches a summary dict from the NuCore backend.
    2. Converts the ``id`` field to an integer (hex strings are accepted).
    3. Enriches the summary with the full routine logic from ``all_routines``.

    The final ``response.output`` is the enriched list of routine summary dicts.
    """

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        dependency_outputs: IntentHandlerResult | None = None,
        framework_context=None,
        route_result=None,
    ) -> dict[str, str]:
        """Refresh the routines database and supply the ``<<routines_database>>`` placeholder.

        Forces a refresh of the NuCore routines database before every call so
        the LLM always receives an up-to-date condensed snapshot.

        Args:
            query:              The user query (unused; reserved for subclass
                                overrides).
            dependency_outputs: Unused for this handler.
            framework_context:  Unused for this handler.
            route_result:       Unused for this handler.

        Returns:
            Dict with ``"<<routines_database>>"`` mapped to a fenced JSON block
            containing the condensed routines snapshot.
        """
        if self.nucore_interface is not None:
            # Ensure we have the latest routines before injecting them into the prompt.
            await self.nucore_interface._refresh_routines_database()

        return {
            "<<routines_database>>": f"```json\n{self.nucore_interface.condensed_routines}\n```",
        }

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: str = None,
        dependency_outputs: IntentHandlerResult | str | dict[str, Any] | None = None,
    ):
        """Filter routines by LLM-scored candidates and enrich with full logic.

        Pipeline:
        1. Build the message list (prompt + routines database).
        2. Call the LLM expecting a JSON/tool response.
        3. Extract tool calls; fall back to a "no match" message when absent.
        4. Fetch and filter routine summaries via :meth:`_get_routine_summary_from_candidates`.
        5. Enrich each summary with the full routine logic from ``all_routines``.

        Args:
            query:               The user query string.
            route_result:        Routing metadata forwarded to message assembly
                                 and stamped on the result.
            framework_context:   Optional extra context string.
            dependency_outputs:  Unused for this handler.

        Returns:
            :class:`~intent_handler.IntentHandlerResult` whose ``output`` is
            the enriched list of routine summary dicts, or a descriptive string
            when no routines match.
        """
        messages = await self.build_messages(
            query,
            dependency_outputs=dependency_outputs,
            framework_context=framework_context,
            route_result=route_result,
        )
        response = await self.call_llm(messages=messages, expect_json=True)

        if isinstance(response, IntentHandlerResult):
            tools = response.get_tool_calls()
            if not tools:
                debug("No tool calls found in the response.")
                response.output = "No routines matched the criteria in the intent."
                response.set_route_result(route_result=route_result)
                return response

            routines = await self._get_routine_summary_from_candidates(tools[0])
            if not routines:
                debug("No matched routines found for the candidates.")
                response.output = "No routines matched the criteria in the intent."
                response.set_route_result(route_result=route_result)
                return response

            # Enrich each summary dict with the full routine logic.
            for rs in routines:
                if rs is None:
                    debug("Received None routine summary from Nucore interface.")
                    response.output = "Received None routine summary from Nucore interface."
                elif 'id' not in rs:
                    debug(f"Routine summary missing 'id' field: {rs}")
                    response.output = f"Routine summary missing 'id' field: {rs}"
                else:
                    routine_id = self._convert_routine_id_to_int(rs['id'])
                    if routine_id is None:
                        debug(f"Failed to convert routine ID {rs['id']} to int, skipping enrichment with full routine logic.")
                        continue
                    rs['id'] = routine_id
                    full_routine = self.nucore_interface.all_routines.get(routine_id)
                    if full_routine is None:
                        debug(f"No full routine found for routine ID: {routine_id}")
                    else:
                        # Attach the complete routine trigger/action logic for downstream use.
                        rs['routine_logic'] = full_routine

            response.output = routines

        response.set_route_result(route_result=route_result)
        return response

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_routine_summary_from_candidates(self, tool) -> list[dict[str, Any]]:
        """Fetch routine summaries for candidates that meet the score threshold.

        Iterates the candidate list in ``tool.args``, discards entries below
        the configured threshold, then calls
        :meth:`~nucore.NuCoreInterface.get_routine_summary` for each passing
        candidate and flattens the results into a single list.

        Args:
            tool: :class:`~intent_handler.adapters.ToolCall` whose ``args`` is
                  a list of dicts with ``routine_id`` and ``score`` keys.

        Returns:
            Flat list of routine summary dicts for all candidates that passed
            the threshold.  Empty when no candidates qualify.
        """
        score_threshold = self.config.get("threshold", 0.80)

        out: list[dict[str, Any]] = []
        candidates = tool.args
        for r in candidates:
            if float(r.get('score', 0)) >= score_threshold:
                try:
                    summaries = await self.nucore_interface.get_routine_summary(r['routine_id'])
                    for summary in summaries:
                        out.append(summary)
                except Exception:
                    pass
        return out

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

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from .models import AgentBudget, AgentStepLog, IntentHandlerResult, ModeDecision, RouteResult


@dataclass(frozen=True)
class BoundedAgentPolicyConfig:
    """Static policy configuration for bounded-agentic mode selection."""

    enabled: bool = False
    enabled_intents: tuple[str, ...] = ()
    budget: AgentBudget = AgentBudget()


class BoundedAgentPolicy:
    """Decides whether a routed request should execute in bounded-agentic mode."""

    def __init__(self, config: BoundedAgentPolicyConfig) -> None:
        self._config = config
        self._enabled_intents = set(config.enabled_intents)

    @classmethod
    def from_runtime_config(cls, runtime_config: dict[str, Any] | None) -> "BoundedAgentPolicy":
        raw = dict((runtime_config or {}).get("bounded_agentic") or {})
        budget = AgentBudget(
            max_steps=max(1, int(raw.get("max_steps", 2))),
            max_retries=max(0, int(raw.get("max_retries", 1))),
            max_latency_ms=max(1, int(raw.get("max_latency_ms", 15000))),
        )
        enabled_intents_raw = raw.get("enabled_intents") or []
        enabled_intents = tuple(
            str(intent).strip()
            for intent in enabled_intents_raw
            if str(intent).strip()
        )
        config = BoundedAgentPolicyConfig(
            enabled=bool(raw.get("enabled", False)),
            enabled_intents=enabled_intents,
            budget=budget,
        )
        return cls(config)

    def decide(self, route_result: RouteResult, intent_config: dict[str, Any] | None = None) -> ModeDecision:
        if not self._config.enabled:
            return ModeDecision(mode="deterministic", reason="bounded_agentic_disabled")

        intent_name = route_result.intent
        if not intent_name:
            return ModeDecision(mode="deterministic", reason="no_routed_intent")

        config = intent_config or {}
        agentic_cfg = config.get("agentic") if isinstance(config.get("agentic"), dict) else {}
        always = bool(agentic_cfg.get("always", False))
        per_intent_enabled = agentic_cfg.get("enabled")
        listed = intent_name in self._enabled_intents

        # Per-intent explicit disable always wins unless "always" is set.
        if per_intent_enabled is False and not always:
            return ModeDecision(mode="deterministic", reason="intent_opted_out")

        if always:
            return ModeDecision(
                mode="bounded_agentic",
                reason="intent_forced_agentic",
                budget=self._config.budget,
            )

        if per_intent_enabled is True:
            reason = "intent_opted_in"
        elif listed:
            reason = "intent_listed_agentic"
        else:
            reason = "agentic_default_all_intents"

        return ModeDecision(
            mode="bounded_agentic",
            reason=reason,
            budget=self._config.budget,
        )


ExecuteChainFn = Callable[[str, RouteResult], Awaitable[list[IntentHandlerResult | None] | None]]
RouteFn = Callable[[str], Awaitable[RouteResult]]
PostStepHookFn = Callable[[], Awaitable[None]]


class BoundedAgentOrchestrator:
    """Runs a bounded plan-act-evaluate loop around intent execution."""

    @staticmethod
    def _resolve_step_query(route: RouteResult, fallback_query: str) -> str:
        """Return the effective query for a step, preferring the route's resolved query."""
        return route.resolved_query or fallback_query

    @staticmethod
    def _is_material_route_change(previous: RouteResult, nxt: RouteResult, previous_query: str, next_query: str) -> bool:
        """Return True when rerouting produced a meaningfully different next step."""
        return (
            previous.intent != nxt.intent
            or previous_query != next_query
            or previous.route_context != nxt.route_context
        )

    @staticmethod
    def _is_context_only_tool_entry(entry: Any) -> bool:
        """Return True when a tool-result entry is only prompt context metadata."""
        return isinstance(entry, dict) and set(entry.keys()) == {"context"}

    @classmethod
    def _has_actionable_tool_result(cls, result: IntentHandlerResult) -> bool:
        """Return True when the result contains a real tool execution payload."""
        if not result.tool_result:
            return False

        entries = result.tool_result if isinstance(result.tool_result, list) else [result.tool_result]
        return any(not cls._is_context_only_tool_entry(entry) for entry in entries)

    async def execute(
        self,
        *,
        query: str,
        initial_route: RouteResult,
        decision: ModeDecision,
        execute_chain: ExecuteChainFn,
        reroute: RouteFn,
        post_step_hook: Optional[PostStepHookFn] = None,
    ) -> list[IntentHandlerResult] | None:
        budget = decision.budget or AgentBudget()
        started = time.perf_counter()
        retries = 0
        step_logs: list[AgentStepLog] = []
        all_step_results: list[IntentHandlerResult] = []
        seen_step_text_outputs: set[str] = set()

        current_query = self._resolve_step_query(initial_route, query)
        current_route = initial_route

        for step in range(1, budget.max_steps + 1):
            # Bounded cycle: execute current plan, refresh state, then replan.
            step_started = time.perf_counter()
            results = await execute_chain(current_query, current_route)
            step_latency_ms = int((time.perf_counter() - step_started) * 1000)

            if results is not None:
                step_results = [r for r in results if isinstance(r, IntentHandlerResult)]
                if not step_results:
                    step_logs.append(
                        AgentStepLog(
                            step=step,
                            intent=current_route.intent,
                            query=current_query,
                            latency_ms=step_latency_ms,
                            status="empty_result",
                            notes="step_returned_no_result",
                        )
                    )
                    break

                new_step_text_outputs: set[str] = set()
                duplicate_output_found = False
                for step_result in step_results:
                    step_text_output = step_result.get_text_output()
                    if not isinstance(step_text_output, str):
                        continue
                    normalized_text = step_text_output.strip()
                    if not normalized_text:
                        continue
                    if normalized_text in seen_step_text_outputs:
                        duplicate_output_found = True
                        break
                    new_step_text_outputs.add(normalized_text)

                if duplicate_output_found:
                    step_logs.append(
                        AgentStepLog(
                            step=step,
                            intent=current_route.intent,
                            query=current_query,
                            latency_ms=step_latency_ms,
                            status="duplicate_output",
                            notes="stopped_on_identical_clarification",
                        )
                    )
                    break

                seen_step_text_outputs.update(new_step_text_outputs)
                all_step_results.extend(step_results)

                should_stop_after_tool = (
                    current_route.intent == "routine_automation"
                    and any(self._has_actionable_tool_result(step_result) for step_result in step_results)
                )

                step_logs.append(
                    AgentStepLog(
                        step=step,
                        intent=current_route.intent,
                        query=current_query,
                        latency_ms=step_latency_ms,
                        status="completed_tool_execution" if should_stop_after_tool else "ok",
                        notes="routine_automation_tool_executed" if should_stop_after_tool else decision.reason,
                    )
                )

                if should_stop_after_tool:
                    break

                total_latency_ms = int((time.perf_counter() - started) * 1000)
                if step >= budget.max_steps or total_latency_ms >= budget.max_latency_ms:
                    break

                # Refresh external state (e.g. device structure) before replanning.
                if post_step_hook is not None:
                    await post_step_hook()

                # Reroute the original query against the refreshed context.
                # If the router returns no intent, all work is done.
                next_route = await reroute(query)
                if not next_route.intent:
                    break

                next_query = self._resolve_step_query(next_route, query)
                # Avoid repeating identical successful steps until max_steps.
                if not self._is_material_route_change(current_route, next_route, current_query, next_query):
                    break

                current_route = next_route
                current_query = next_query
                continue

            step_logs.append(
                AgentStepLog(
                    step=step,
                    intent=current_route.intent,
                    query=current_query,
                    latency_ms=step_latency_ms,
                    status="empty_result",
                    notes="step_returned_no_result",
                )
            )

            total_latency_ms = int((time.perf_counter() - started) * 1000)
            if total_latency_ms >= budget.max_latency_ms:
                break
            if retries >= budget.max_retries:
                break

            retries += 1
            current_route = await reroute(current_query)
            current_query = self._resolve_step_query(current_route, current_query)

        if all_step_results:
            return self._aggregate_results(
                results=all_step_results,
                step_logs=step_logs,
                decision=decision,
                budget=budget,
                initial_intent=initial_route.intent or "",
            )

        # Provide a stable result object even if all bounded attempts fail.
        return [IntentHandlerResult(
            intent=initial_route.intent or "",
            output={
                "text": "I could not complete this request within the configured bounded-agentic limits.",
            },
            execution_metadata={
                "mode": "bounded_agentic",
                "reason": decision.reason,
                "budget": {
                    "max_steps": budget.max_steps,
                    "max_retries": budget.max_retries,
                    "max_latency_ms": budget.max_latency_ms,
                },
                "steps": [log.__dict__ for log in step_logs],
                "status": "budget_exhausted",
            },
        )]

    def _aggregate_results(
        self,
        *,
        results: list[IntentHandlerResult],
        step_logs: list[AgentStepLog],
        decision: ModeDecision,
        budget: AgentBudget,
        initial_intent: str,
    ) -> list[IntentHandlerResult]:
        """Merge results from multiple steps into a single IntentHandlerResult."""
        combined_tool_results: list[Any] = []
        for r in results:
            if r.tool_result:
                if isinstance(r.tool_result, list):
                    combined_tool_results.extend(r.tool_result)
                else:
                    combined_tool_results.append(r.tool_result)

        final = results[-1]
        final.tool_result = combined_tool_results if combined_tool_results else final.tool_result
        final.execution_metadata = {
            "mode": "bounded_agentic",
            "reason": decision.reason,
            "steps_executed": len(results),
            "budget": {
                "max_steps": budget.max_steps,
                "max_retries": budget.max_retries,
                "max_latency_ms": budget.max_latency_ms,
            },
            "steps": [log.__dict__ for log in step_logs],
        }
        ### MICHEL 
        # return result 
        return [final]

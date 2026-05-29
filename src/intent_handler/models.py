from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .adapters import ToolCall
from intent_handler.stream_handler import StreamHandler


# ---------------------------------------------------------------------------
# Intent definition
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class IntentDefinition:
    """Immutable descriptor for a single intent handler loaded from disk.

    Instances are created by :class:`~loader.IntentHandlerRegistry` during
    :meth:`~loader.IntentHandlerRegistry.refresh` and are never mutated
    afterwards.  All path fields are absolute.

    Attributes:
        name:                  Canonical intent name (must match directory name
                               and ``config["intent"]``).
        directory:             Absolute path to the intent's sub-directory.
        config_path:           Absolute path to ``config.json``.
        prompt_content:        Fully-expanded prompt template text (common
                               module placeholders already substituted).
        handler_path:          Absolute path to the handler ``.py`` file.
        stream_handler_path:   Absolute path to the optional stream handler
                               ``.py`` file, or ``None``.
        description:           Human-readable description from ``config.json``.
        handler_class:         Explicit class name to load from ``handler_path``,
                               or ``None`` to auto-discover the sole subclass.
        stream_handler_class:  Pre-instantiated :class:`~stream_handler.StreamHandler`
                               instance, or ``None`` when no stream handler is
                               configured.
        routing_examples:      Example queries used to guide the router.
        router_hints:          Additional free-text hints for the router.
        llm_config:            Per-intent LLM override config (merged on top of
                               the runtime default at call time).
        config:                Full raw ``config.json`` dict for arbitrary
                               field access.
    """

    name: str
    directory: Path
    config_path: Path
    prompt_content: str
    handler_path: Path
    stream_handler_path: Path
    description: str
    handler_class: str | None = None
    stream_handler_class: StreamHandler | None = None
    routing_examples: list[str] = field(default_factory=list)
    router_hints: list[str] = field(default_factory=list)
    llm_config: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RoutePlanStep:
    """One router-planned intent execution step.

    Attributes:
        intent:        Intent name to execute for this step.
        user_query:    Step-specific query text to send to that intent.
        route_context: Optional context for downstream prompt placeholders.
        notes:         Optional planner notes for observability/debugging.
    """

    intent: str
    user_query: str
    route_context: dict[str, Any] | None = None
    notes: str | None = None

@dataclass(frozen=True)
class RouteResult:
    """Immutable result returned by :class:`~router.IntentRouter`.

    Attributes:
        intent:          Name of the selected intent handler.
        confidence:      Optional confidence score in ``[0, 1]`` reported by
                         the routing LLM.
        notes:           Optional reasoning or explanation from the router.
        route_context:   Optional dict of additional context or metadata produced
                         by the router for downstream intent handlers.
        resolved_query:  Optionally rewritten/clarified version of the original
                         user query produced during routing.
        route_plan:      Optional ordered list of planned intent steps for
                 multi-intent requests.
        raw_response:    Full raw response dict from the routing LLM for
                         debugging or downstream inspection.
    """

    intent: str
    confidence: float | None = None
    notes: str | None = None
    route_context: dict[str, Any] | None = None
    resolved_query: str | None = None
    route_plan: list[RoutePlanStep] | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Bounded agentic orchestration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentBudget:
    """Execution limits applied to bounded-agentic requests.

    Attributes:
        max_steps:      Maximum planner/execution loop iterations.
        max_retries:    Maximum reroute retries after failed steps.
        max_latency_ms: Soft latency budget for the full request.
    """

    max_steps: int = 2
    max_retries: int = 1
    max_latency_ms: int = 15000


@dataclass(frozen=True)
class ModeDecision:
    """Router-adjacent decision describing how a request should execute.

    Attributes:
        mode:    ``"deterministic"`` or ``"bounded_agentic"``.
        reason:  Human-readable policy reason for observability.
        budget:  Budget applied when ``mode`` is ``"bounded_agentic"``.
    """

    mode: str
    reason: str
    budget: AgentBudget | None = None


@dataclass(frozen=True)
class AgentStepLog:
    """One step in a bounded-agentic execution trace."""

    step: int
    intent: str | None
    query: str
    latency_ms: int
    status: str
    notes: str | None = None


# ---------------------------------------------------------------------------
# Handler result
# ---------------------------------------------------------------------------

@dataclass(frozen=False)
class IntentHandlerResult:
    """Mutable result accumulator produced by a single intent handler execution.

    A handler populates this object during its run; the runtime then reads it
    back to deliver the response to the caller.

    Attributes:
        intent:         Name of the intent that produced this result.
        output:         Primary LLM output dict (keys: ``text``, ``content``,
                        ``tool_calls``, ``tool_results``, etc.) or any value
                        set by the handler.
        effective_query: Optionally rewritten/clarified version of the original
                         user query produced during handling (e.g. by a
                         clarification subroutine).
        route_result:   :class:`RouteResult` that selected this intent, if
                        routing was performed.
        tool_result:    Accumulated list of tool execution results appended via
                        :meth:`add_tool_result`.
        stream_handler: Optional :class:`~stream_handler.StreamHandler` instance
                        for streaming responses.
    """

    intent: str
    output: Any
    effective_query: str | None = None
    route_result: RouteResult | None = None
    tool_result: list[Any] | None = None
    stream_handler: StreamHandler | None = None
    execution_metadata: dict[str, Any] | None = None

    def set_output(self, output: Any) -> None:
        """Replace the primary output value."""
        self.output = output

    def get_stream_handler(self) -> StreamHandler | None:
        """Return the attached stream handler, or ``None``."""
        return self.stream_handler

    def add_tool_result_context(self, context: Any) -> None:
        """Append context to the internal list of tool results.
        No-ops when ``context`` is ``None``.
        """
        if context is None:
            return
        if self.tool_result is None:
            self.tool_result = []
        self.tool_result.append({"context": context})

    def add_tool_result(self, tool_result: Any) -> None:
        """Append a tool execution result to the internal list.

        No-ops when ``tool_result`` is ``None``.
        """
        if tool_result is None:
            return
        if self.tool_result is None:
            self.tool_result = []
        self.tool_result.append(tool_result)

    def set_route_result(self, route_result: RouteResult | None = None) -> None:
        """Attach the :class:`RouteResult` that selected this intent."""
        self.route_result = route_result

    def set_effective_query(self, effective_query: str | None = None) -> None:
        """Attach the effective query that was used for this intent."""
        self.effective_query = effective_query

    def get_effective_query(self) -> str | None:
        """Return the effective query that was used for this intent."""
        return self.effective_query

    def get_text_output(self) -> str | None:
        """Extract the best available plain-text output string.

        Priority order:
        1. ``tool_result`` — returned as-is if already a string, otherwise
           coerced with ``str()``.
        2. ``output["text"]``   — first non-empty string value.
        3. ``output["content"]`` — fallback for providers that use this key.

        Returns ``None`` when none of the above yield a non-empty string.
        """
        if self.tool_result:
            return self.tool_result if isinstance(self.tool_result, str) else str(self.tool_result)

        if isinstance(self.output, dict):
            text = self.output.get("text", None)
            if isinstance(text, str) and text.strip():
                return text

            content = self.output.get("content", None)
            if isinstance(content, str) and content.strip():
                return content

        return None

    def get_tool_calls(self) -> list[ToolCall]:
        """Parse and return all tool calls from ``output["tool_calls"]``.

        Handles the canonical dict format ``{"id": ..., "name": ...,
        "input": {...}}`` produced by the LLM adapters after normalisation.
        The ``input`` dict is unwrapped one level when it contains a nested
        ``"args"`` key (legacy provider shape).

        Returns an empty list when ``output`` is absent or not a dict.
        """
        tool_calls: list[ToolCall] = []
        if self.output is None or not isinstance(self.output, dict):
            return tool_calls
        tools = self.output.get("tool_calls")
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, dict) and "name" in tool:
                    args = {}
                    try:
                        args = tool.get("input", {})
                        # Unwrap nested "args" key produced by some providers.
                        if "args" in args:
                            args = args["args"]
                    except Exception:
                        args = {}
                    tool_calls.append(ToolCall(
                        call_id=tool.get("id", ""),
                        name=tool["name"],
                        args=args,
                        provider=tool.get("provider", ""),
                        raw=tool.get("raw", None),
                    ))
        return tool_calls

    def get_tool_results(self) -> list[Any] | None:
        """Return accumulated tool results.

        Prefers ``self.tool_result`` (populated via :meth:`add_tool_result`)
        and falls back to ``output["tool_results"]`` for handlers that embed
        results directly in the output dict.

        Returns ``None`` when no tool results are available.
        """
        if self.tool_result is not None:
            return self.tool_result
        if self.output is None or not isinstance(self.output, dict):
            return None
        return self.output.get("tool_results", None)


# ---------------------------------------------------------------------------
# Conversation history
# ---------------------------------------------------------------------------

@dataclass
class ConversationTurn:
    """A single query/response pair in a conversation.

    Attributes:
        query:    The user's input text for this turn.
        response: The assistant's response text for this turn.
    """

    query: str
    response: str


@dataclass
class ConversationHistory:
    """Rolling window of recent :class:`ConversationTurn` objects.

    Older turns are automatically evicted when the window exceeds
    ``max_turns``.

    Attributes:
        turns:     Ordered list of turns from oldest to newest.
        max_turns: Maximum number of turns to retain (default 20).
    """

    turns: list[ConversationTurn] = field(default_factory=list)
    max_turns: int = 20

    def append(self, query: str, response: str) -> None:
        """Add a new turn and evict the oldest if the window is full."""
        self.turns.append(ConversationTurn(query=query, response=response))
        if len(self.turns) > self.max_turns:
            self.turns = self.turns[-self.max_turns:]

    def recent(self, n: int | None = None) -> list[ConversationTurn]:
        """Return the ``n`` most recent turns, or all turns when ``n`` is ``None``."""
        if n is None:
            return list(self.turns)
        return self.turns[-n:]
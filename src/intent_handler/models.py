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
        previous_dependencies: Names of intents that must run before this one.
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
    previous_dependencies: list[str] = field(default_factory=list)
    routing_examples: list[str] = field(default_factory=list)
    router_hints: list[str] = field(default_factory=list)
    llm_config: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RouteResult:
    """Immutable result returned by :class:`~router.IntentRouter`.

    Attributes:
        intent:          Name of the selected intent handler.
        confidence:      Optional confidence score in ``[0, 1]`` reported by
                         the routing LLM.
        notes:           Optional reasoning or explanation from the router.
        resolved_query:  Optionally rewritten/clarified version of the original
                         user query produced during routing.
        raw_response:    Full raw response dict from the routing LLM for
                         debugging or downstream inspection.
    """

    intent: str
    confidence: float | None = None
    notes: str | None = None
    resolved_query: str | None = None
    raw_response: dict[str, Any] = field(default_factory=dict)


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
        route_result:   :class:`RouteResult` that selected this intent, if
                        routing was performed.
        tool_result:    Accumulated list of tool execution results appended via
                        :meth:`add_tool_result`.
        stream_handler: Optional :class:`~stream_handler.StreamHandler` instance
                        for streaming responses.
    """

    intent: str
    output: Any
    route_result: RouteResult | None = None
    tool_result: list[Any] | None = None
    stream_handler: StreamHandler | None = None

    def set_output(self, output: Any) -> None:
        """Replace the primary output value."""
        self.output = output

    def get_stream_handler(self) -> StreamHandler | None:
        """Return the attached stream handler, or ``None``."""
        return self.stream_handler

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
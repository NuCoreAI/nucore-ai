from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, ClassVar

from .adapters import ToolCall
from .stream_handler import StreamHandler


# ---------------------------------------------------------------------------
# Handler result
# ---------------------------------------------------------------------------

@dataclass(frozen=False)
class IntentHandlerResult:
    """Mutable result accumulator produced by a single runtime turn.

    Attributes:
        intent:         Name of the intent/runtime that produced this result.
        output:         Primary LLM output dict (keys: ``text``, ``content``,
                        ``tool_calls``, ``tool_results``, etc.) or any value
                        set by the caller.
        tool_result:    Accumulated list of tool execution results appended via
                        :meth:`add_tool_result`.
        stream_handler: Optional :class:`~stream_handler.StreamHandler` instance
                        for streaming responses.
    """

    intent: str
    output: Any
    tool_result: list[Any] | None = None
    stream_handler: StreamHandler | None = None
    execution_metadata: dict[str, Any] | None = None

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

            notes = self.output.get("notes", None)
            if isinstance(notes, str) and notes.strip():
                return notes

        return None

    def get_tool_calls(self) -> list[ToolCall]:
        """Parse and return all tool calls from ``output["tool_calls"]``.

        Handles the canonical dict format ``{"id": ..., "name": ...,
        "input": {...}}`` produced by the LLM adapters after normalisation.
        The ``input`` dict is unwrapped one level when it contains a nested
        ``"args"`` key (legacy provider shape). Every tool's ``args`` is
        declared as a JSON array in its schema, but some models over-
        serialize it into a JSON-encoded string instead of real structured
        JSON; that's recovered with a best-effort ``json.loads``. If the
        result still isn't a list, it's dropped rather than handed to a
        handler that expects to iterate dict entries out of it.

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
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except Exception:
                                    args = []
                            if not isinstance(args, list):
                                args = []
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
        turns:            Ordered list of turns from oldest to newest.
        max_turns:        Maximum number of turns to retain (default 20).
        pending_intent:   Name of the intent that is mid-clarification (asked
                          the user something and has not yet completed its
                          task) or whose last attempt failed, or ``None`` when
                          nothing is pending.
        pending_question: The clarifying text the pending intent asked (or a
                          description of the failure), used to give the
                          caller context on the next turn.
        pending_failed:   ``True`` when ``pending_intent`` is pending because
                          its last attempt errored, rather than because it
                          asked a clarifying question.
        pending_turns:    Number of consecutive turns answered without
                          explicitly resolving or abandoning the pending
                          clarification. See :meth:`note_pending_still_unresolved`.
    """

    PENDING_TTL_TURNS: ClassVar[int] = 3
    # Sentinel for ``pending_intent`` when the clarifying question itself came
    # from a generic (non-intent-specific) answer rather than from a specific
    # intent handler -- there is no real intent name to attribute it to, but
    # the pending-clarification machinery still needs to track that something
    # is awaiting a reply.
    ROUTER_PENDING: ClassVar[str] = "__router__"

    turns: list[ConversationTurn] = field(default_factory=list)
    max_turns: int = 20
    pending_intent: str | None = None
    pending_question: str | None = None
    pending_failed: bool = False
    pending_turns: int = 0

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

    def set_pending(self, intent: str | None, question: str | None, *, failed: bool = False) -> None:
        """Record that ``intent`` is mid-clarification, awaiting a reply to ``question``.

        Pass ``failed=True`` when this is being set because the intent's last
        attempt errored, rather than because it asked the user a question.
        """
        self.pending_intent = intent
        self.pending_question = question
        self.pending_failed = failed
        self.pending_turns = 0

    def clear_pending(self) -> None:
        """Clear any pending clarification state (task completed, or abandoned)."""
        self.pending_intent = None
        self.pending_question = None
        self.pending_failed = False
        self.pending_turns = 0

    def note_pending_still_unresolved(self) -> None:
        """Record that a turn passed without resolving or abandoning the
        pending clarification.

        A single such turn is *not* treated as abandonment -- a soft hint can
        be missed on one bare/ambiguous reply (e.g. "1") without that
        permanently discarding otherwise-recoverable context. But this can't
        be trusted to self-correct forever either, so after
        :attr:`PENDING_TTL_TURNS` consecutive misses the pending state is
        force-cleared so a stuck session can't wedge indefinitely.
        """
        if not self.pending_intent:
            return
        self.pending_turns += 1
        if self.pending_turns >= self.PENDING_TTL_TURNS:
            self.clear_pending()

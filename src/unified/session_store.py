from __future__ import annotations

import asyncio

from .models import ConversationHistory


class SessionStore:
    """In-memory store mapping session IDs to :class:`~models.ConversationHistory` objects.

    Sessions are created on first access and live for the lifetime of the
    process (or, when shared across connections -- see
    ``run_unified_runtime._run_websocket_server`` -- for as long as this one
    ``SessionStore`` instance is kept alive, which is what lets a
    reconnecting client's conversation history actually survive the
    reconnect instead of starting over empty).

    Not safe for concurrent *mutation* of one session's history on its own --
    two requests for the same session ID racing a read-generate-append
    sequence against the same :class:`~models.ConversationHistory` object
    would interleave. :meth:`lock` is the external locking this class's
    caller (``UnifiedRuntime.handle_query``) is expected to hold for that
    critical section; a second concurrent request for the same session then
    waits for the first to fully finish instead of racing it.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ConversationHistory] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Keyed by identity alone (client_id::user_id -- see
        # EisyUIContext.get_identity_key), never by
        # f"{identity}::{tool_set}" the way _sessions is: the customer's
        # current screen doesn't change just because the chatbot switched
        # between unified/plugin_authoring (see merged-toolsets.md), so one
        # slot per identity is deliberately shared across whichever tool set
        # is active. Survives a reconnect (a new websocket connection, same
        # customer) the same way _sessions already does, for the same
        # reason: this store outlives any one connection.
        self._ui_contexts: dict[str, dict | None] = {}

    def lock(self, session_id: str) -> asyncio.Lock:
        """Returns this session's lock, creating it on first use -- same
        lazy-creation pattern as :meth:`get` uses for ``_sessions``. Always
        returns the same object for the same ``session_id``, so callers that
        ``async with`` it genuinely serialize against each other."""
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    def get(self, session_id: str, max_turns: int = 20) -> ConversationHistory:
        """Return the :class:`~models.ConversationHistory` for ``session_id``.

        Creates a new history object with ``max_turns`` when the session is
        seen for the first time.  On subsequent calls the existing object is
        returned as-is; ``max_turns`` has no effect after creation.

        Args:
            session_id: Arbitrary string key identifying the conversation.
            max_turns:  Maximum number of turns to retain in the rolling
                        window.  Only applied at session creation time.

        Returns:
            The :class:`~models.ConversationHistory` for the session.
        """
        if session_id not in self._sessions:
            self._sessions[session_id] = ConversationHistory(max_turns=max_turns)
        return self._sessions[session_id]

    def clear(self, session_id: str) -> None:
        """Remove the history for a single session.

        No-ops silently when ``session_id`` is not present.
        """
        self._sessions.pop(session_id, None)

    def clear_all(self) -> None:
        """Remove history for all sessions."""
        self._sessions.clear()

    def get_ui_context(self, identity_key: str) -> dict | None:
        """Last known UI context (the Eisy UI's own per-turn ``context``
        payload shape -- current screen, etc.) for *identity_key*, or
        ``None`` if nothing has been recorded for it yet.

        This is what lets a brand-new websocket connection (a reconnect --
        same customer, new connection object, so a fresh, empty
        ``EisyUIContext`` -- see that class's docstring) pick up where the
        last connection left off instead of looking like the customer's
        screen is suddenly unknown. See :meth:`set_ui_context` for who
        writes this.
        """
        return self._ui_contexts.get(identity_key)

    def set_ui_context(self, identity_key: str, context: dict | None) -> None:
        """Record *context* as the latest known UI context for
        *identity_key*. Called by ``EisyUIContext.process_message`` every
        time a ``"type": "context"`` message arrives and identity
        (client_id/user_id) is resolvable -- not locked, same as every
        other field on ``EisyUIContext`` itself: a single dict-key
        assignment never interleaves under asyncio's cooperative
        scheduling, so there's nothing a lock would protect here.
        """
        self._ui_contexts[identity_key] = context

    def format_history_for_prompt(self, session_id: str) -> str:
        """Format conversation history with consistent labeling for LLM prompts.

        Returns a formatted string suitable for inclusion in user message content,
        with labeled sections and begin/end markers. Returns empty string if no
        history exists.

        Args:
            session_id: Session identifier to retrieve history for.

        Returns:
            Formatted history string with "CONVERSATION HISTORY" label,
            begin/end markers, and turns in chronological order.
            Empty string if history is empty or nonexistent.
        """
        history = self.get(session_id)
        return self._format_history_content(history)

    @staticmethod
    def _format_history_content(history: ConversationHistory | None) -> str:
        """Format a ConversationHistory with consistent labeling (static helper).

        This can be called on any history object without session_store access.

        Args:
            history: The ConversationHistory to format, or None.

        Returns:
            Formatted history string with labels and markers, or empty string.
        """
        if not history or not history.turns:
            return ""

        content = (
            "---\n# CONVERSATION HISTORY (oldest first):\n"
            "\n<<BEGIN CONVERSATION HISTORY>>\n"
            "\n**NEVER** use conversation history content as source of truth for real time states of devices, routines, or other entities. Always call the relevant APIs to get the latest information. \n"
        )
        for index, turn in enumerate(history.turns, start=1):
            content += f"---\n## Turn {index}:"
            content += f"\n- **User**: {turn.query.strip()}\n"
            content += f"\n- **Assistant**: {turn.response.strip()}\n\n"
        content += "<<END CONVERSATION HISTORY>>"
        return content

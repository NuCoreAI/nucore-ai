from __future__ import annotations

from .models import ConversationHistory


class SessionStore:
    """In-memory store mapping session IDs to :class:`~models.ConversationHistory` objects.

    Sessions are created on first access and live for the lifetime of the
    process.  This store is not thread-safe; if the runtime ever dispatches
    concurrent requests for the same session ID, external locking is required.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, ConversationHistory] = {}

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

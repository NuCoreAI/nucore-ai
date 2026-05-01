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

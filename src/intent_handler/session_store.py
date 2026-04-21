from __future__ import annotations

from .models import ConversationHistory


class SessionStore:
    """In-memory store of per-session conversation history."""

    def __init__(self) -> None:
        self._sessions: dict[str, ConversationHistory] = {}

    def get(self, session_id: str, max_turns: int = 20) -> ConversationHistory:
        """Return the ConversationHistory for session_id, creating it if absent.
        
        max_turns is only applied when the session is first created.
        """
        if session_id not in self._sessions:
            self._sessions[session_id] = ConversationHistory(max_turns=max_turns)
        return self._sessions[session_id]

    def clear(self, session_id: str) -> None:
        """Remove history for a specific session."""
        self._sessions.pop(session_id, None)

    def clear_all(self) -> None:
        """Remove history for all sessions."""
        self._sessions.clear()

from __future__ import annotations

import asyncio
import datetime
from pathlib import Path
from typing import Any

from utils import get_logger

from .models import ConversationHistory, ConversationTurn
from .session_persistence import SessionPersistence, history_from_dict, history_to_dict

logger = get_logger(__name__)

_DEFAULT_SESSION_NAME_TEMPLATE = "Session {id}"


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class SessionStore:
    """In-memory store mapping session IDs to :class:`~models.ConversationHistory` objects,
    with optional disk persistence layered on top.

    Sessions are created on first access and live for the lifetime of the
    process (or, when shared across connections -- see
    ``run_unified_runtime._run_websocket_server`` -- for as long as this one
    ``SessionStore`` instance is kept alive, which is what lets a
    reconnecting client's conversation history actually survive the
    reconnect instead of starting over empty).

    When *persist_dir* is given, a **durable** session (one backed by a
    real ``user_id``, not the per-connection uuid4/``"default"`` fallback --
    see :meth:`resolve_current_session_id`'s ``durable`` flag) also survives
    a full process restart, and a user can keep more than one named session
    per tool set (``start_new_session``/``list_sessions``/``load_session``/
    ``rename_session``/``delete_session``). *persist_dir* is ``None`` by
    default, which keeps every one of those methods a pure in-memory no-op
    -- zero new disk I/O, zero behavior change -- for every caller that
    doesn't pass one (the REPL, and any test constructing a bare
    ``SessionStore()``).

    Not safe for concurrent *mutation* of one session's history on its own --
    two requests for the same session ID racing a read-generate-append
    sequence against the same :class:`~models.ConversationHistory` object
    would interleave. :meth:`lock` is the external locking this class's
    caller (``UnifiedRuntime.handle_query``) is expected to hold for that
    critical section; a second concurrent request for the same session then
    waits for the first to fully finish instead of racing it.
    """

    def __init__(self, persist_dir: str | Path | None = None) -> None:
        self._sessions: dict[str, ConversationHistory] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        # Keyed by identity (user_id) alone, never tool-set-suffixed: the
        # customer's current screen doesn't change just because the chatbot
        # switched between unified/plugin_authoring (see
        # merged-toolsets.md). Survives a reconnect the same way _sessions
        # does, for the same reason: this store outlives any one connection.
        # Deliberately never persisted to disk -- current-screen state is
        # volatile and carries clientId, which this whole feature exists to
        # stop relying on.
        self._ui_contexts: dict[str, dict | None] = {}
        self._persistence = SessionPersistence(persist_dir) if persist_dir else None
        # Which session id is "current" for a given (user_id, tool_set) --
        # an in-memory cache so the hot path (every turn) never needs a
        # disk read just to find out. Only a session-management command or
        # a first-use-this-process bootstrap (see
        # resolve_current_session_id) ever touches the index file itself.
        self._current_session_cache: dict[str, int] = {}
        # In-memory-cached index (session list + current pointer) per
        # (user_id, tool_set) -- see _get_index's docstring for why this
        # cache is what makes the read-modify-write cycle in every
        # session-management method below actually atomic under its write
        # lock, instead of two concurrent commands each reading stale disk
        # state and clobbering each other.
        self._index_cache: dict[str, dict[str, Any]] = {}
        # One write lock per on-disk artifact (a session file, an index
        # file, or meta.json) -- separate from `_locks` (which guards the
        # in-memory read-generate-append critical section) so a
        # session-management command (e.g. /rename-session) can take its
        # own narrow lock without needing the full turn-processing lock.
        self._write_locks: dict[str, asyncio.Lock] = {}

    @property
    def persistence_enabled(self) -> bool:
        return self._persistence is not None

    @staticmethod
    def composite_key(identity: str, tool_set: str, session_num: int | None = None) -> str:
        """The in-memory ``_sessions``/``lock`` key for one conversation.

        ``session_num=None`` (the default) returns ``f"{identity}::{tool_set}"``
        -- today's exact shape, used for a non-durable session (the
        per-connection uuid4/``"default"`` fallback) where there is no
        durable identity to scope multiple named sessions under in the
        first place. A durable session includes its resolved session
        number (see :meth:`resolve_current_session_id`) so two different
        saved sessions for the same user+tool_set never collide in memory.
        """
        base = f"{identity}::{tool_set}"
        return base if session_num is None else f"{base}::s{session_num}"

    def _write_lock(self, key: str) -> asyncio.Lock:
        if key not in self._write_locks:
            self._write_locks[key] = asyncio.Lock()
        return self._write_locks[key]

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
        returned as-is; ``max_turns`` has no effect after creation. Purely
        in-memory -- never consults disk, even when persistence is
        configured; see :meth:`get_or_load` for the persistence-aware
        counterpart.

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
        payload shape -- current screen, etc.) for *identity_key* (the
        customer's ``user_id``), or ``None`` if nothing has been recorded
        for it yet.

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
        *identity_key* (the customer's ``user_id``). Called by
        ``EisyUIContext.process_message`` every time a ``"type": "context"``
        message arrives and a user_id is known -- not locked, same as every
        other field on ``EisyUIContext`` itself: a single dict-key
        assignment never interleaves under asyncio's cooperative
        scheduling, so there's nothing a lock would protect here.
        """
        self._ui_contexts[identity_key] = context

    # ------------------------------------------------------------------
    # Persistence -- active tool set ("mode")
    # ------------------------------------------------------------------

    async def load_active_tool_set(self, user_id: str) -> str | None:
        """The last saved active tool set for *user_id*, or ``None`` when
        persistence is off or nothing has been saved yet. Never raises."""
        if self._persistence is None:
            return None
        try:
            data = await asyncio.to_thread(self._persistence.read_meta, user_id)
        except Exception:
            logger.warning("session_store: failed to load active tool set for a user", exc_info=True)
            return None
        return data.get("active_tool_set") if data else None

    async def save_active_tool_set(self, user_id: str, tool_set: str) -> None:
        """Persist *tool_set* as the active mode for *user_id*. A no-op
        when persistence is off. Swallows and logs any write failure --
        a failed save must never fail the turn that triggered it."""
        if self._persistence is None:
            return
        async with self._write_lock(f"{user_id}::meta"):
            try:
                await asyncio.to_thread(
                    self._persistence.write_meta,
                    user_id,
                    {"active_tool_set": tool_set, "saved_at": _now_iso()},
                )
            except Exception:
                logger.warning("session_store: failed to save active tool set for a user", exc_info=True)

    # ------------------------------------------------------------------
    # Persistence -- which session is "current" per (user_id, tool_set)
    # ------------------------------------------------------------------

    def _bootstrap_index(self) -> dict[str, Any]:
        now = _now_iso()
        return {
            "current_session_id": 1,
            "next_session_id": 2,
            "sessions": [
                {
                    "id": 1,
                    "name": _DEFAULT_SESSION_NAME_TEMPLATE.format(id=1),
                    "created_at": now,
                    "updated_at": now,
                    "turn_count": 0,
                }
            ],
        }

    async def _get_index(self, user_id: str, tool_set: str) -> dict[str, Any]:
        """The in-memory-cached index for ``(user_id, tool_set)`` --
        **the same dict object** on every call for this pair, not a copy,
        so every caller mutates (and must only mutate) it while holding
        this pair's write lock (``self._write_lock(f"{user_id}::{tool_set}::index")``)
        -- see every public session-management method below for the
        pattern. Without that lock, two concurrent mutations would each
        read-modify-write against stale disk state and silently clobber
        each other; caching the index in memory (the same pattern
        ``_sessions`` already uses for conversation history) means every
        call after the first is a cheap in-memory read, and the lock alone
        is then enough to make the whole read-modify-write atomic.

        Loads from disk (via ``asyncio.to_thread``) and caches on first
        access this process; bootstraps (and immediately persists) a fresh
        index on a miss, so a user who never touches the session-management
        commands still gets exactly one continuously-growing session,
        matching today's single-history behavior by default.
        """
        cache_key = f"{user_id}::{tool_set}"
        if cache_key in self._index_cache:
            return self._index_cache[cache_key]
        assert self._persistence is not None
        try:
            index = await asyncio.to_thread(self._persistence.read_index, user_id, tool_set)
        except Exception:
            logger.warning("session_store: failed to read session index", exc_info=True)
            index = None
        if index is None:
            index = self._bootstrap_index()
            await self._save_index(user_id, tool_set, index)
        self._index_cache[cache_key] = index
        return index

    async def _save_index(self, user_id: str, tool_set: str, index: dict[str, Any]) -> None:
        """Write *index* to disk. Callers are expected to already hold
        this pair's write lock -- this method itself does not acquire one,
        so it can be called from within a caller's own locked
        read-modify-write section without deadlocking on a non-reentrant
        ``asyncio.Lock``."""
        assert self._persistence is not None
        try:
            await asyncio.to_thread(self._persistence.write_index, user_id, tool_set, index)
        except Exception:
            logger.warning("session_store: failed to write session index", exc_info=True)

    async def resolve_current_session_id(self, user_id: str, tool_set: str, *, durable: bool) -> int | None:
        """The session number currently active for *user_id* in *tool_set*.

        Returns ``None`` when ``durable`` is false -- the caller then uses
        :meth:`composite_key` with no session number, i.e. today's exact
        non-durable shape (the per-connection uuid4/``"default"`` fallback
        has no stable identity to scope multiple named sessions under).

        When ``durable``: returns the cached value if one is already known
        for this ``(user_id, tool_set)`` this process. Otherwise, with
        persistence configured, loads/bootstraps the index (see
        :meth:`_get_index`) and caches its ``current_session_id``. Without
        persistence configured, returns a fixed in-memory-only sentinel so
        a durable caller still gets a stable key without ever touching disk.
        """
        if not durable:
            return None
        cache_key = f"{user_id}::{tool_set}"
        if cache_key in self._current_session_cache:
            return self._current_session_cache[cache_key]
        if self._persistence is None:
            self._current_session_cache[cache_key] = 1
            return 1
        async with self._write_lock(f"{user_id}::{tool_set}::index"):
            index = await self._get_index(user_id, tool_set)
            current = int(index.get("current_session_id", 1))
        self._current_session_cache[cache_key] = current
        return current

    # ------------------------------------------------------------------
    # Persistence -- one session's conversation history
    # ------------------------------------------------------------------

    async def get_or_load(
        self,
        identity: str,
        tool_set: str,
        session_num: int | None,
        *,
        max_turns: int = 20,
        durable: bool = False,
    ) -> ConversationHistory:
        """Persistence-aware counterpart to :meth:`get`.

        Returns the in-memory entry if already cached. Otherwise, when not
        ``durable`` or persistence is off, behaves exactly like :meth:`get`
        -- no new I/O. Otherwise loads from disk (re-checking the in-memory
        cache after the await, in case something else filled it meanwhile),
        falling back to a fresh, empty history on a miss or any load error.
        """
        key = self.composite_key(identity, tool_set, session_num)
        if key in self._sessions:
            return self._sessions[key]
        if not durable or self._persistence is None or session_num is None:
            return self.get(key, max_turns)

        try:
            data = await asyncio.to_thread(self._persistence.read_session, identity, tool_set, session_num)
        except Exception:
            logger.warning("session_store: failed to load a persisted session", exc_info=True)
            data = None

        if key in self._sessions:  # Filled concurrently while we were awaiting disk.
            return self._sessions[key]

        history = history_from_dict(data.get("history") if data else None, max_turns=max_turns) if data else None
        self._sessions[key] = history if history is not None else ConversationHistory(max_turns=max_turns)
        return self._sessions[key]

    async def persist(
        self,
        identity: str,
        tool_set: str,
        session_num: int | None,
        *,
        durable: bool = False,
    ) -> None:
        """Write-through save of one session's current history to disk.

        A no-op unless ``durable``, persistence is configured, *and* the
        key is already cached in memory (nothing to save otherwise). The
        snapshot is taken synchronously (``history_to_dict`` deep-copies
        via ``dataclasses.asdict``) before the write is handed off to a
        thread, so a later in-memory mutation of the same history can't
        race what gets written. Also updates this session's ``turn_count``/
        ``updated_at`` in the index. Swallows and logs every exception --
        a failed save must never fail the turn that already completed.
        """
        if not durable or self._persistence is None or session_num is None:
            return
        key = self.composite_key(identity, tool_set, session_num)
        history = self._sessions.get(key)
        if history is None:
            return
        snapshot = history_to_dict(history)
        async with self._write_lock(f"{identity}::{tool_set}::session_{session_num}"):
            try:
                await asyncio.to_thread(
                    self._persistence.write_session,
                    identity,
                    tool_set,
                    session_num,
                    {"history": snapshot},
                )
            except Exception:
                logger.warning("session_store: failed to persist a session", exc_info=True)
                return
        await self._touch_session_entry(identity, tool_set, session_num, turn_count=len(history.turns))

    async def _touch_session_entry(self, user_id: str, tool_set: str, session_id: int, *, turn_count: int) -> None:
        async with self._write_lock(f"{user_id}::{tool_set}::index"):
            index = await self._get_index(user_id, tool_set)
            changed = False
            for entry in index.get("sessions", []):
                if entry.get("id") == session_id:
                    entry["turn_count"] = turn_count
                    entry["updated_at"] = _now_iso()
                    changed = True
                    break
            if changed:
                await self._save_index(user_id, tool_set, index)

    # ------------------------------------------------------------------
    # Session management -- /new-session, /sessions, /load-session,
    # /rename-session, /delete-session
    # ------------------------------------------------------------------

    async def list_sessions(self, user_id: str, tool_set: str) -> list[dict[str, Any]]:
        """All saved sessions for *user_id* in *tool_set*, oldest-id first.
        Empty when persistence is off (there's nothing durable to list)."""
        if self._persistence is None:
            return []
        async with self._write_lock(f"{user_id}::{tool_set}::index"):
            index = await self._get_index(user_id, tool_set)
            return list(index.get("sessions", []))

    async def start_new_session(self, user_id: str, tool_set: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Start a new, empty session and make it current.

        Returns ``(previous_entry, new_entry)`` -- *previous_entry* is
        whichever session was current before this call (already fully
        persisted under its own id/file -- nothing extra to "archive"),
        or ``None`` if this is the very first session ever for this user
        and tool set.
        """
        async with self._write_lock(f"{user_id}::{tool_set}::index"):
            index = await self._get_index(user_id, tool_set)
            sessions = index.setdefault("sessions", [])
            previous_id = index.get("current_session_id")
            previous_entry = next((e for e in sessions if e.get("id") == previous_id), None)

            new_id = int(index.get("next_session_id", len(sessions) + 1))
            now = _now_iso()
            new_entry = {
                "id": new_id,
                "name": _DEFAULT_SESSION_NAME_TEMPLATE.format(id=new_id),
                "created_at": now,
                "updated_at": now,
                "turn_count": 0,
            }
            sessions.append(new_entry)
            index["next_session_id"] = new_id + 1
            index["current_session_id"] = new_id
            await self._save_index(user_id, tool_set, index)
        self._current_session_cache[f"{user_id}::{tool_set}"] = new_id
        return previous_entry, new_entry

    async def load_session(self, user_id: str, tool_set: str, session_id: int) -> dict[str, Any] | None:
        """Make *session_id* the current session for *user_id*/*tool_set*.
        Returns that session's index entry, or ``None`` if no such id
        exists (nothing is changed in that case)."""
        async with self._write_lock(f"{user_id}::{tool_set}::index"):
            index = await self._get_index(user_id, tool_set)
            entry = next((e for e in index.get("sessions", []) if e.get("id") == session_id), None)
            if entry is None:
                return None
            index["current_session_id"] = session_id
            await self._save_index(user_id, tool_set, index)
        self._current_session_cache[f"{user_id}::{tool_set}"] = session_id
        return entry

    async def rename_session(self, user_id: str, tool_set: str, session_id: int, name: str) -> bool:
        """Rename *session_id*. Returns ``False`` if no such id exists."""
        async with self._write_lock(f"{user_id}::{tool_set}::index"):
            index = await self._get_index(user_id, tool_set)
            entry = next((e for e in index.get("sessions", []) if e.get("id") == session_id), None)
            if entry is None:
                return False
            entry["name"] = name
            entry["updated_at"] = _now_iso()
            await self._save_index(user_id, tool_set, index)
        return True

    async def delete_session(
        self, user_id: str, tool_set: str, session_id: int
    ) -> tuple[dict[str, Any], dict[str, Any] | None] | None:
        """Delete *session_id*. Returns ``(deleted_entry, replacement_entry)``
        -- *replacement_entry* is set only when a fresh session had to be
        auto-created, which happens whenever the deleted session was
        current or was the last one left (a user always has at least one
        live session). Returns ``None`` if no such id exists."""
        async with self._write_lock(f"{user_id}::{tool_set}::index"):
            index = await self._get_index(user_id, tool_set)
            sessions = index.get("sessions", [])
            entry = next((e for e in sessions if e.get("id") == session_id), None)
            if entry is None:
                return None
            sessions[:] = [e for e in sessions if e.get("id") != session_id]
            was_current = index.get("current_session_id") == session_id
            try:
                await asyncio.to_thread(self._persistence.delete_session_file, user_id, tool_set, session_id)
            except Exception:
                logger.warning("session_store: failed to delete a session file", exc_info=True)
            self._sessions.pop(self.composite_key(user_id, tool_set, session_id), None)

            replacement: dict[str, Any] | None = None
            if was_current or not sessions:
                new_id = int(index.get("next_session_id", session_id + 1))
                now = _now_iso()
                replacement = {
                    "id": new_id,
                    "name": _DEFAULT_SESSION_NAME_TEMPLATE.format(id=new_id),
                    "created_at": now,
                    "updated_at": now,
                    "turn_count": 0,
                }
                sessions.append(replacement)
                index["next_session_id"] = new_id + 1
                index["current_session_id"] = new_id
                self._current_session_cache[f"{user_id}::{tool_set}"] = new_id

            await self._save_index(user_id, tool_set, index)
        return entry, replacement

    # ------------------------------------------------------------------
    # Formatting
    # ------------------------------------------------------------------

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
    def _render_turns(turns: list[ConversationTurn]) -> str:
        """Render *turns* as labeled ``Turn N`` blocks -- the shared loop
        behind both :meth:`_format_history_content` (LLM-prompt-facing) and
        :meth:`format_history_for_display` (human-facing, via the
        ``/load-session`` command)."""
        content = ""
        for index, turn in enumerate(turns, start=1):
            content += f"---\n## Turn {index}:"
            content += f"\n- **User**: {turn.query.strip()}\n"
            content += f"\n- **Assistant**: {turn.response.strip()}\n\n"
        return content

    @classmethod
    def _format_history_content(cls, history: ConversationHistory | None) -> str:
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
        content += cls._render_turns(history.turns)
        content += "<<END CONVERSATION HISTORY>>"
        return content

    @classmethod
    def format_history_for_display(cls, history: ConversationHistory | None, *, mode_label: str) -> str:
        """Human-facing rendering of *history* for the ``/load-session``
        command -- same per-turn rendering as the LLM-facing formatter,
        without the admonition line aimed at the model rather than a
        person. Empty string when there are no turns."""
        if not history or not history.turns:
            return ""
        return f"Conversation history ({mode_label} mode, {len(history.turns)} turn(s)):\n\n" + cls._render_turns(
            history.turns
        )

    @staticmethod
    def format_sessions_table(entries: list[dict[str, Any]], *, current_id: int | None) -> str:
        """Render *entries* (as returned by :meth:`list_sessions`) as a
        plain-text table for the ``/sessions`` command: ``id`` / ``name`` /
        ``description`` columns, a header separator, and a horizontal rule
        between every row so each session is visually distinct in a plain
        chat bubble or terminal (no markdown table rendering assumed)."""
        headers = ("id", "name", "description")
        rows: list[tuple[str, str, str]] = []
        for entry in entries:
            name = entry.get("name") or _DEFAULT_SESSION_NAME_TEMPLATE.format(id=entry.get("id"))
            turns = entry.get("turn_count", 0)
            updated = entry.get("updated_at", "unknown")
            description = f"{turns} turn(s), last used {updated}"
            if entry.get("id") == current_id:
                description += " (current)"
            rows.append((str(entry.get("id")), name, description))

        widths = [
            max(len(headers[i]), *(len(row[i]) for row in rows)) if rows else len(headers[i])
            for i in range(3)
        ]

        def _fmt_row(row: tuple[str, str, str]) -> str:
            padded = [cell.ljust(width) for cell, width in zip(row[:-1], widths[:-1])]
            padded.append(row[-1])
            return " | ".join(padded)

        separator = "-+-".join("-" * width for width in widths)
        lines = [_fmt_row(headers), separator]
        for row in rows:
            lines.append(_fmt_row(row))
            lines.append(separator)
        return "\n".join(lines)

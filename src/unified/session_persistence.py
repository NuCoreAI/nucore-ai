"""Synchronous, on-disk persistence for per-user conversation sessions.

No ``asyncio`` here at all, deliberately -- every method is plain blocking
file I/O, called by :class:`~session_store.SessionStore` via
``asyncio.to_thread`` so the event loop is never blocked. Keeping the I/O in
its own synchronous class (rather than mixing it into ``SessionStore``
itself) means it can be unit-tested directly, with no event loop, the same
way ``preferences/preference_store.py`` is.

Layout, one directory per user (named by a hash of ``user_id``, never the
plaintext -- ``user_id`` arrives unvalidated from an inbound JSON payload,
so hashing removes any path-traversal risk a raw value could carry), one
subdirectory per tool set::

    <root>/<sha256(user_id)>/meta.json                    # active tool set
    <root>/<sha256(user_id)>/<tool_set>/index.json         # session list + current pointer
    <root>/<sha256(user_id)>/<tool_set>/session_<id>.json  # one session's history

Every read tolerates a missing or corrupt file by returning ``None`` --
never raises -- mirroring ``PreferenceStore``'s "start empty" convention.
Every write is atomic (temp file in the same directory, then ``os.replace``)
and goes through :func:`~utils.secret_redaction.redact` first, the same
``redact(json.dumps(...))`` pattern ``utils/prompt_log.py`` already applies
at its own write path, so an already-registered secret never lands on disk
even inside a raw conversation transcript.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from utils import get_logger, redact

from .models import ConversationHistory, ConversationTurn

logger = get_logger(__name__)

_VERSION = 1
_TOOL_SET_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode("utf-8")).hexdigest()


def history_to_dict(history: ConversationHistory) -> dict[str, Any]:
    """Serialise *history* to a plain JSON-safe dict. ``dataclasses.asdict``
    handles the nested ``turns`` list automatically; the two ``ClassVar``s
    (``PENDING_TTL_TURNS``/``ROUTER_PENDING``) are class attributes, not
    instance fields, so ``asdict`` never touches them."""
    return dataclasses.asdict(history)


_TURN_FIELDS = {f.name for f in dataclasses.fields(ConversationTurn)}


def history_from_dict(data: Any, *, max_turns: int) -> ConversationHistory | None:
    """Reconstruct a :class:`ConversationHistory` from :func:`history_to_dict`
    output (or anything shaped like it). Each turn dict is filtered down to
    :class:`ConversationTurn`'s own known fields first, so a file written by
    an older or newer version of this schema (an added/removed field)
    doesn't crash the load. *max_turns* always comes from the **current**
    profile config, not whatever was stored -- loading from disk counts as
    "session creation" for that rule, the same as ``SessionStore.get``.
    Returns ``None`` on any shape error rather than raising -- the caller
    falls back to a fresh, empty history."""
    if not isinstance(data, dict):
        return None
    try:
        raw_turns = data.get("turns", [])
        if not isinstance(raw_turns, list):
            return None
        turns = [
            ConversationTurn(**{k: v for k, v in turn.items() if k in _TURN_FIELDS})
            for turn in raw_turns
            if isinstance(turn, dict)
        ]
        history = ConversationHistory(
            turns=turns,
            max_turns=max_turns,
            pending_intent=data.get("pending_intent"),
            pending_question=data.get("pending_question"),
            pending_failed=bool(data.get("pending_failed", False)),
            pending_turns=int(data.get("pending_turns", 0)),
        )
        if len(history.turns) > history.max_turns:
            history.turns = history.turns[-history.max_turns :]
        return history
    except Exception:
        logger.warning("session_persistence: failed to reconstruct history from stored data", exc_info=True)
        return None


class SessionPersistence:
    """Blocking file-system access for persisted sessions. See module
    docstring for the on-disk layout and every method's never-raise-on-read
    convention."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)

    def user_dir(self, user_id: str) -> Path:
        return self._root / _hash_user_id(user_id)

    def _tool_set_dir(self, user_id: str, tool_set: str) -> Path:
        if not _TOOL_SET_NAME_RE.match(tool_set):
            raise ValueError(f"Unsafe tool set name for on-disk persistence: {tool_set!r}")
        return self.user_dir(user_id) / tool_set

    def _meta_path(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "meta.json"

    def _index_path(self, user_id: str, tool_set: str) -> Path:
        return self._tool_set_dir(user_id, tool_set) / "index.json"

    def _session_path(self, user_id: str, tool_set: str, session_id: int) -> Path:
        return self._tool_set_dir(user_id, tool_set) / f"session_{session_id}.json"

    # ------------------------------------------------------------------
    # Generic read/write helpers
    # ------------------------------------------------------------------

    def _read(self, path: Path, *, user_id: str) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("session_persistence: corrupt JSON at %s", path, exc_info=True)
            return None
        if not isinstance(data, dict) or data.get("version") != _VERSION:
            logger.warning("session_persistence: unreadable/unversioned data at %s", path)
            return None
        if data.get("user_id") != user_id:
            # Hash collision, or a hand-copied file -- never trust content
            # whose own recorded identity doesn't match the lookup key.
            logger.warning("session_persistence: user_id mismatch at %s", path)
            return None
        return data

    def _write(self, path: Path, data: dict[str, Any], *, user_id: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # chmod both the immediate parent (a tool-set subdirectory, for a
        # session/index file) and the user's own top-level directory (for
        # meta.json, path.parent already *is* this) -- a transcript must
        # never be group/world-readable at any level.
        for directory in {path.parent, self.user_dir(user_id)}:
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass  # Best-effort -- not every filesystem supports POSIX permission bits.
        text = redact(json.dumps(data, default=str))
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.", suffix=".tmp")
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            try:
                os.chmod(tmp_path, 0o600)
            except OSError:
                pass
            os.replace(tmp_path, path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    # ------------------------------------------------------------------
    # meta.json -- active tool set
    # ------------------------------------------------------------------

    def read_meta(self, user_id: str) -> dict[str, Any] | None:
        return self._read(self._meta_path(user_id), user_id=user_id)

    def write_meta(self, user_id: str, meta: dict[str, Any]) -> None:
        self._write(self._meta_path(user_id), {"version": _VERSION, "user_id": user_id, **meta}, user_id=user_id)

    # ------------------------------------------------------------------
    # index.json -- session list + current pointer, per tool set
    # ------------------------------------------------------------------

    def read_index(self, user_id: str, tool_set: str) -> dict[str, Any] | None:
        data = self._read(self._index_path(user_id, tool_set), user_id=user_id)
        if data is not None and data.get("tool_set") != tool_set:
            return None
        return data

    def write_index(self, user_id: str, tool_set: str, index: dict[str, Any]) -> None:
        self._write(
            self._index_path(user_id, tool_set),
            {"version": _VERSION, "user_id": user_id, "tool_set": tool_set, **index},
            user_id=user_id,
        )

    # ------------------------------------------------------------------
    # session_<id>.json -- one session's conversation history
    # ------------------------------------------------------------------

    def read_session(self, user_id: str, tool_set: str, session_id: int) -> dict[str, Any] | None:
        data = self._read(self._session_path(user_id, tool_set, session_id), user_id=user_id)
        if data is not None and (data.get("tool_set") != tool_set or data.get("session_id") != session_id):
            return None
        return data

    def write_session(self, user_id: str, tool_set: str, session_id: int, payload: dict[str, Any]) -> None:
        self._write(
            self._session_path(user_id, tool_set, session_id),
            {"version": _VERSION, "user_id": user_id, "tool_set": tool_set, "session_id": session_id, **payload},
            user_id=user_id,
        )

    def delete_session_file(self, user_id: str, tool_set: str, session_id: int) -> None:
        self._session_path(user_id, tool_set, session_id).unlink(missing_ok=True)

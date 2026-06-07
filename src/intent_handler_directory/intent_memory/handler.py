from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from threading import Lock
from typing import Any

from intent_handler import BaseIntentHandler, IntentHandlerResult
from utils import get_logger

logger = get_logger(__name__)


class IntentMemorySQLiteStore:
    """SQLite-backed per-intent memory store with deterministic CRUD behavior."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;

                CREATE TABLE IF NOT EXISTS intent_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    intent TEXT NOT NULL,
                    section TEXT NOT NULL,
                    key TEXT,
                    entry_markdown TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'explicit_user',
                    confidence REAL,
                    context TEXT,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );

                CREATE INDEX IF NOT EXISTS idx_intent_memory_intent ON intent_memory(intent);
                CREATE INDEX IF NOT EXISTS idx_intent_memory_intent_section ON intent_memory(intent, section);
                CREATE INDEX IF NOT EXISTS idx_intent_memory_intent_key ON intent_memory(intent, key);

                CREATE UNIQUE INDEX IF NOT EXISTS uq_intent_memory_intent_section_key
                ON intent_memory(intent, section, key)
                WHERE key IS NOT NULL;
                """
            )

    @staticmethod
    def _normalize_section(section: str | None) -> str:
        raw = (section or "USER DEFINED").strip()
        return raw if raw else "USER DEFINED"

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "intent": row["intent"],
            "section": row["section"],
            "key": row["key"],
            "entry_markdown": row["entry_markdown"],
            "source": row["source"],
            "confidence": row["confidence"],
            "context": row["context"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create(
        self,
        *,
        intent: str,
        section: str | None,
        entry_markdown: str,
        key: str | None = None,
        source: str = "explicit_user",
        confidence: float | None = None,
        context: str | None = None,
    ) -> dict[str, Any]:
        section_name = self._normalize_section(section)
        entry = (entry_markdown or "").strip()
        if not entry:
            raise ValueError("entry_markdown is required")

        with self._lock, self._connect() as conn:
            if key:
                conn.execute(
                    """
                    INSERT INTO intent_memory(intent, section, key, entry_markdown, source, confidence, context)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(intent, section, key)
                    DO UPDATE SET
                        entry_markdown = excluded.entry_markdown,
                        source = excluded.source,
                        confidence = excluded.confidence,
                        context = excluded.context,
                        updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                    """,
                    (intent, section_name, key, entry, source, confidence, context),
                )
                row = conn.execute(
                    "SELECT * FROM intent_memory WHERE intent = ? AND section = ? AND key = ?",
                    (intent, section_name, key),
                ).fetchone()
            else:
                cur = conn.execute(
                    """
                    INSERT INTO intent_memory(intent, section, key, entry_markdown, source, confidence, context)
                    VALUES (?, ?, NULL, ?, ?, ?, ?)
                    """,
                    (intent, section_name, entry, source, confidence, context),
                )
                row = conn.execute(
                    "SELECT * FROM intent_memory WHERE id = ?",
                    (cur.lastrowid,),
                ).fetchone()

        if row is None:
            raise RuntimeError("Failed to create memory record")
        return self._row_to_record(row)

    def retrieve(
        self,
        *,
        intent: str,
        section: str | None = None,
        key: str | None = None,
        query: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        filters: list[str] = ["intent = ?"]
        params: list[Any] = [intent]

        if section:
            filters.append("section = ?")
            params.append(self._normalize_section(section))
        if key:
            filters.append("key = ?")
            params.append(key)
        if query:
            filters.append("(entry_markdown LIKE ? OR context LIKE ?)")
            like = f"%{query}%"
            params.extend([like, like])

        safe_limit = max(1, min(int(limit), 500))
        sql = (
            "SELECT * FROM intent_memory "
            f"WHERE {' AND '.join(filters)} "
            "ORDER BY updated_at DESC, id DESC LIMIT ?"
        )
        params.append(safe_limit)

        with self._lock, self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def update(
        self,
        *,
        intent: str,
        record_id: int | None = None,
        key: str | None = None,
        section: str | None = None,
        entry_markdown: str | None = None,
        source: str | None = None,
        confidence: float | None = None,
        context: str | None = None,
    ) -> dict[str, Any]:
        if record_id is None and not key:
            raise ValueError("update requires record_id or key")

        section_name = self._normalize_section(section)
        with self._lock, self._connect() as conn:
            if record_id is not None:
                current = conn.execute(
                    "SELECT * FROM intent_memory WHERE id = ? AND intent = ?",
                    (record_id, intent),
                ).fetchone()
            else:
                current = conn.execute(
                    "SELECT * FROM intent_memory WHERE intent = ? AND section = ? AND key = ?",
                    (intent, section_name, key),
                ).fetchone()

            if current is None:
                raise KeyError("Memory record not found")

            next_entry = entry_markdown if entry_markdown is not None else current["entry_markdown"]
            next_source = source if source is not None else current["source"]
            next_confidence = confidence if confidence is not None else current["confidence"]
            next_context = context if context is not None else current["context"]

            conn.execute(
                """
                UPDATE intent_memory
                SET entry_markdown = ?, source = ?, confidence = ?, context = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                WHERE id = ?
                """,
                (next_entry, next_source, next_confidence, next_context, current["id"]),
            )
            row = conn.execute("SELECT * FROM intent_memory WHERE id = ?", (current["id"],)).fetchone()

        if row is None:
            raise RuntimeError("Failed to update memory record")
        return self._row_to_record(row)

    def delete(
        self,
        *,
        intent: str,
        record_id: int | None = None,
        key: str | None = None,
        section: str | None = None,
    ) -> int:
        if record_id is None and not key and not section:
            raise ValueError("delete requires record_id or key or section")

        with self._lock, self._connect() as conn:
            if record_id is not None:
                cur = conn.execute(
                    "DELETE FROM intent_memory WHERE id = ? AND intent = ?",
                    (record_id, intent),
                )
                return int(cur.rowcount)

            where = ["intent = ?"]
            params: list[Any] = [intent]
            if section:
                where.append("section = ?")
                params.append(self._normalize_section(section))
            if key:
                where.append("key = ?")
                params.append(key)

            cur = conn.execute(f"DELETE FROM intent_memory WHERE {' AND '.join(where)}", params)
            return int(cur.rowcount)

    def render_markdown(self, *, intent: str) -> str:
        records = self.retrieve(intent=intent, limit=500)
        if not records:
            return ""

        grouped: dict[str, list[dict[str, Any]]] = {}
        for rec in reversed(records):
            grouped.setdefault(str(rec.get("section", "USER DEFINED")), []).append(rec)

        lines = ["# INTENT MEMORY"]
        for section_name, section_records in grouped.items():
            lines.append("")
            lines.append(f"## {section_name}")
            for rec in section_records:
                text = str(rec.get("entry_markdown", "")).strip()
                if not text:
                    continue
                lines.append(text)

        return "\n".join(lines).strip()


_MEMORY_DB_PATH = Path(
    os.getenv(
        "NUCORE_INTENT_MEMORY_DB_PATH",
        str(Path(__file__).resolve().parents[2] / "intent_handler" / "runtime_assets" / "memory_store" / "intent_memory.sqlite3"),
    )
).expanduser().resolve()
_MEMORY_STORE = IntentMemorySQLiteStore(_MEMORY_DB_PATH)


class IntentMemoryIntentHandler(BaseIntentHandler):
    """Intent handler providing SQLite-backed memory CRUD for other intents."""

    async def get_memory_context(
        self,
        *,
        target_intent: str,
        query: str | None = None,
        route_result=None,
        framework_context: dict | None = None,
    ) -> dict[str, Any] | None:
        """Return normalized memory facts for ``target_intent``.

        This function is called by runtime to hydrate each intent invocation
        with persisted facts. Runtime should not access storage directly.
        """
        if not target_intent:
            return None

        records = _MEMORY_STORE.retrieve(intent=target_intent, limit=200)
        if not records:
            return None

        markdown = _MEMORY_STORE.render_markdown(intent=target_intent)
        facts: list[dict[str, Any]] = []
        for rec in records:
            facts.append(
                {
                    "id": rec.get("id"),
                    "section": rec.get("section"),
                    "key": rec.get("key"),
                    "entry_markdown": rec.get("entry_markdown"),
                    "source": rec.get("source"),
                    "confidence": rec.get("confidence"),
                    "context": rec.get("context"),
                    "updated_at": rec.get("updated_at"),
                }
            )

        return {
            "intent_memory": {
                "intent": target_intent,
                "markdown": markdown,
                "facts": facts,
            },
            "intent_memory_markdown": markdown,
        }

    async def get_prompt_runtime_replacements(
        self,
        query,
        *,
        framework_context=None,
        route_result=None,
    ) -> dict[str, str]:
        return {}

    async def handle(
        self,
        query,
        *,
        route_result=None,
        framework_context: dict | None = None,
        raw_response: IntentHandlerResult | None = None,
        tool_calls=None,
    ) -> IntentHandlerResult | None:
        response = raw_response
        if response is None:
            return None

        tools = tool_calls if tool_calls is not None else response.get_tool_calls()
        if tools:
            for tool in tools:
                if tool.name == "tool_intent_memory":
                    result = self._process_intent_memory_tool(tool)
                else:
                    result = f"Unknown tool called: {tool.name}"
                response.add_tool_result(tool_result=result)

        response.set_route_result(route_result=route_result)
        return response

    def _process_intent_memory_tool(self, tool) -> dict[str, Any] | list[dict[str, Any]] | str:
        if tool is None or tool.args is None:
            return "Invalid tool call: missing arguments"

        payload = tool.args
        if isinstance(payload, list):
            if not payload:
                return "Invalid tool call: empty arguments"
            if isinstance(payload[0], dict):
                payload = payload[0]

        if not isinstance(payload, dict):
            return "Invalid tool call: arguments must be an object"

        action = str(payload.get("action", "")).strip().lower()
        target_intent = str(payload.get("intent", "")).strip()
        if not target_intent:
            return "Invalid tool call: intent is required"

        section = payload.get("section")
        key = payload.get("key")
        record_id = payload.get("id")
        entry_markdown = payload.get("entry_markdown")
        source = payload.get("source", "explicit_user")
        confidence = payload.get("confidence")
        context = payload.get("context")
        query = payload.get("query")
        limit = payload.get("limit", 100)

        try:
            if action == "create":
                created = _MEMORY_STORE.create(
                    intent=target_intent,
                    section=section,
                    key=key,
                    entry_markdown=entry_markdown,
                    source=source,
                    confidence=confidence,
                    context=context,
                )
                return {"status": "ok", "action": action, "record": created}

            if action == "read":
                records = _MEMORY_STORE.retrieve(
                    intent=target_intent,
                    section=section,
                    key=key,
                    query=query,
                    limit=limit,
                )
                return [{"record": rec} for rec in records]

            if action == "update":
                updated = _MEMORY_STORE.update(
                    intent=target_intent,
                    record_id=record_id,
                    key=key,
                    section=section,
                    entry_markdown=entry_markdown,
                    source=source,
                    confidence=confidence,
                    context=context,
                )
                return {"status": "ok", "action": action, "record": updated}

            if action == "delete":
                deleted = _MEMORY_STORE.delete(
                    intent=target_intent,
                    record_id=record_id,
                    key=key,
                    section=section,
                )
                return {"status": "ok", "action": action, "deleted": deleted}

            if action == "render_markdown":
                markdown = _MEMORY_STORE.render_markdown(intent=target_intent)
                return {"status": "ok", "action": action, "markdown": markdown}

            return f"Unsupported memory action: {action}"
        except Exception as exc:
            logger.warning("Memory tool failure: %s", exc)
            return f"Error processing memory tool: {exc}"

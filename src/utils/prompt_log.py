"""Debug logging of full LLM interactions (system prompt + every tool
call/result) -- a JSONL log under a configurable directory, replacing the
old hardcoded ``/tmp/nucore.prompt.md`` Python-``repr()`` dump.

Format: one JSON object per line, one line per message *content block*
(never a whole message folded into one line), so a text block and a
tool_use block from the same assistant turn become two lines instead of one
dense Python-repr blob -- this is what makes ``grep '"tool_use_id":"..."'``
find a call and its result in one shot instead of hand-pairing lines. The
system prompt is written once per unique content (hash-deduped), not
re-embedded on every agentic-loop iteration like the old code did -- that
repetition was confirmed to be the dominant source of bloat in the old file.

Size management: the active file is pruned once it crosses *max_bytes* --
the oldest lines are peeled off into a dated ``.jsonl.gz`` archive (never
just discarded), and pruning runs on a worker thread (``asyncio.to_thread``)
since a multi-MB compress-and-rewrite must never stall the event loop --
``write()`` sits on the agentic loop's hot path, called before every LLM
call. Nothing is ever deleted at startup -- a restart just keeps appending
to whatever active file already exists.
"""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import os
import uuid
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .logger import get_logger

logger = get_logger(__name__)

DEFAULT_FILENAME = "nucore.prompt.jsonl"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
_MAX_TRACKED_RUNS = 200


class PromptLogManager:
    """Writes LLM interaction turns to a JSONL debug log, pruning into dated
    gzip archives once the active file crosses *max_bytes*.

    *log_dir* only ever answers "where" -- *enabled* is the sole "whether"
    switch; there's no overloaded empty-path-means-off behavior. Logging is
    on by default.
    """

    def __init__(
        self,
        log_dir: Path | str,
        *,
        enabled: bool = True,
        filename: str = DEFAULT_FILENAME,
        max_bytes: int = DEFAULT_MAX_BYTES,
    ) -> None:
        self.enabled = enabled
        self.log_dir = Path(log_dir)
        self.filename = filename
        self.max_bytes = max_bytes
        self.path = self.log_dir / self.filename
        if self.enabled:
            self.log_dir.mkdir(parents=True, exist_ok=True)

        self._session_id = uuid.uuid4().hex[:8]
        self._logged_system_prompt_hash: str | None = None
        # id(messages) -> (the list itself, how many of its entries are
        # already on disk). Lets write() be called with the *entire*
        # accumulated messages list on every iteration (as AgenticLoop
        # already does) while only the new tail actually gets appended --
        # messages is the same mutable list object across iterations of one
        # turn (loop.py extends it in place), and a fresh object every new
        # turn. id() alone is NOT safe here: a short-lived messages list
        # gets garbage-collected once its turn ends, and CPython readily
        # reuses that same address for the very next turn's list, which
        # would otherwise look like "already logged" and silently swallow
        # it. Keeping a strong reference alongside the count keeps a
        # currently-tracked list's id() from being reused by anything else
        # for as long as it's tracked, and the `is` check on lookup catches
        # the rare case where a collision slips through anyway (falls back
        # to treating it as unseen, i.e. never silently drops messages).
        # Bounded via LRU eviction so a long-running process doesn't hold
        # onto every turn's messages list forever.
        self._logged_counts: "OrderedDict[int, tuple[list, int]]" = OrderedDict()

    async def write(self, intent_name: str, messages: list[dict[str, Any]]) -> None:
        if not self.enabled:
            return
        try:
            new_messages = self._new_messages_since_last_write(messages)
            if not new_messages:
                return

            lines: list[dict[str, Any]] = []
            for msg in new_messages:
                lines.extend(self._render_message(intent_name, msg))
            if not lines:
                return

            text = "".join(json.dumps(line, default=str) + "\n" for line in lines)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(text)

            await self._maybe_prune()
        except Exception as ex:
            # Debug logging must never break the actual conversation.
            logger.error(f"prompt log write failed: {ex}")

    def _new_messages_since_last_write(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        key = id(messages)
        entry = self._logged_counts.get(key)
        already = entry[1] if entry is not None and entry[0] is messages else 0
        new_messages = messages[already:]
        self._logged_counts[key] = (messages, len(messages))
        self._logged_counts.move_to_end(key)
        while len(self._logged_counts) > _MAX_TRACKED_RUNS:
            self._logged_counts.popitem(last=False)
        return new_messages

    def _render_message(self, intent_name: str, msg: dict[str, Any]) -> list[dict[str, Any]]:
        role = msg.get("role", "?")
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        base = {"ts": ts, "session": self._session_id, "intent": intent_name, "role": role}

        if role == "system":
            content = msg.get("content")
            text = content if isinstance(content, str) else json.dumps(content, default=str)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            if digest == self._logged_system_prompt_hash:
                return []
            self._logged_system_prompt_hash = digest
            return [{**base, "kind": "system_prompt", "hash": digest, "content": text}]

        lines = []
        for block in self._iter_blocks(msg):
            rendered = self._render_block(block)
            # A tool_result block lives inside a "user"-role message on the
            # wire (that's the Anthropic API shape), which reads as
            # confusing in a log -- relabel just that line, not the whole
            # message, since a message can mix a tool_use with other blocks.
            line_role = "tool" if rendered.get("kind") == "tool_result" else role
            lines.append({**base, "role": line_role, **rendered})
        return lines

    @staticmethod
    def _iter_blocks(msg: dict[str, Any]) -> list[Any]:
        content = msg.get("content")
        if content is None:
            gemini_parts = msg.get("gemini_parts")
            if gemini_parts is None:
                return []  # genuinely nothing to log, not a fallback to the raw message dict
            content = gemini_parts
        if isinstance(content, list):
            return content
        if isinstance(content, str):
            return [{"type": "text", "text": content}] if content else []
        return [{"type": "text", "text": str(content)}]

    @staticmethod
    def _parse_maybe_json(value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return value
        return value

    def _render_block(self, block: Any) -> dict[str, Any]:
        if not isinstance(block, dict):
            return {"kind": "raw", "raw": block}

        block_type = block.get("type")
        if block_type == "tool_use":
            return {
                "kind": "tool_use",
                "tool": block.get("name"),
                "tool_use_id": block.get("id"),
                "input": block.get("input"),
            }
        if block_type == "tool_result":
            return {
                "kind": "tool_result",
                "tool_use_id": block.get("tool_use_id"),
                "content": self._parse_maybe_json(block.get("content")),
            }
        if block_type == "text":
            return {"kind": "text", "text": block.get("text")}
        # Gemini native parts carry no "type" key at all -- see gemini_adapter.py's
        # build_tool_round_trip_messages.
        if "functionCall" in block:
            call = block["functionCall"]
            return {
                "kind": "tool_use",
                "tool": call.get("name"),
                "tool_use_id": call.get("id"),
                "input": call.get("args"),
            }
        if "functionResponse" in block:
            resp = block["functionResponse"]
            return {
                "kind": "tool_result",
                "tool": resp.get("name"),
                "tool_use_id": resp.get("id"),
                "content": resp.get("response"),
            }
        if "text" in block:
            return {"kind": "text", "text": block.get("text")}
        return {"kind": "raw", "raw": block}

    async def _maybe_prune(self) -> None:
        try:
            size = os.path.getsize(self.path)
        except FileNotFoundError:
            return
        if size <= self.max_bytes:
            return
        try:
            await asyncio.to_thread(self._prune)
        except Exception as ex:
            logger.error(f"prompt log prune failed: {ex}")

    def _prune(self) -> None:
        """Runs on a worker thread (see _maybe_prune) -- peels the oldest
        lines off the active JSONL file into a dated gzip archive, keeping
        the newest lines (down to half of max_bytes, so the very next write
        doesn't immediately re-trigger) plus the most recent system_prompt
        line if pruning would otherwise drop it entirely."""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return

        target = self.max_bytes // 2
        kept: list[str] = []
        kept_size = 0
        for line in reversed(lines):
            kept.append(line)
            kept_size += len(line.encode("utf-8"))
            if kept_size >= target:
                break
        kept.reverse()
        pruned = lines[: len(lines) - len(kept)]
        if not pruned:
            return

        last_system_prompt = None
        for line in pruned:
            if self._safe_kind(line) == "system_prompt":
                last_system_prompt = line
        if last_system_prompt is not None and not any(
            self._safe_kind(line) == "system_prompt" for line in kept
        ):
            kept.insert(0, last_system_prompt)

        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
        archive_path = self.log_dir / f"{Path(self.filename).stem}.{stamp}.jsonl.gz"
        with gzip.open(archive_path, "wt", encoding="utf-8") as f:
            f.writelines(pruned)

        with open(self.path, "w", encoding="utf-8") as f:
            f.writelines(kept)

    @staticmethod
    def _safe_kind(line: str) -> str | None:
        try:
            return json.loads(line).get("kind")
        except ValueError:
            return None


_manager: PromptLogManager | None = None


def configure_prompt_logging(log_dir: str | Path, *, enabled: bool = True) -> PromptLogManager:
    """Configure the process-wide prompt log manager once at startup."""
    global _manager
    _manager = PromptLogManager(log_dir, enabled=enabled)
    return _manager


def get_prompt_log_manager() -> PromptLogManager:
    """Returns the configured singleton, or a safe ``<cwd>/logs``-default one
    if ``configure_prompt_logging()`` was never called (e.g. a script that
    exercises ``AgenticLoop`` directly without going through the CLI entry
    point)."""
    global _manager
    if _manager is None:
        _manager = PromptLogManager(Path.cwd() / "logs")
    return _manager

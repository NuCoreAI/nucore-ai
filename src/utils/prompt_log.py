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

The same duplication problem also shows up *across* turns, not just within
one: ``UnifiedRuntime.handle_query`` rebuilds ``history_messages`` from
``SessionStore`` from scratch every turn and hands ``AgenticLoop.run()`` a
brand-new ``messages`` list object each time, so the id()-based "only the
new tail" tracking below (correct *within* one turn, where the same object
is extended in place) never recognizes a new turn's list as a continuation
-- the entire reconstructed history would get re-rendered to disk on every
single turn. ``_logged_line_hashes``/``_is_duplicate_line`` fixes this with
a second, content-based dedup layer: any rendered line whose exact content
was already written (scoped by the caller's ``conversation_id``, so two
different real conversations with coincidentally identical text don't
suppress each other) is dropped instead of rewritten. Confirmed against a
real 32-minute conversation's log: 750 logged ``text`` lines, only 129
distinct; 52 ``tool_use`` lines, only 38 distinct -- this is what that fixes.
The accepted tradeoff: the exact same literal text at two genuinely
different real moments *within* one conversation (e.g. the identical
canned reply to the same demo question asked twice) now also dedupes
together, logging only the first occurrence. Acceptable for a debug/
cache-analysis log, not an audit trail.

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
_MAX_TRACKED_SYSTEM_PROMPTS = 512
_MAX_TRACKED_LINES = 4096


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
        # A bounded set of already-logged system-prompt hashes, not just the
        # last one: every turn sends a static section followed by a volatile
        # tail (see prompt_builder.build_system_prompt_sections), so with a
        # single "last hash" the ~100KB static section would never match
        # the previous line (the tail) and would be re-logged every turn.
        self._logged_system_prompt_hashes: "OrderedDict[str, None]" = OrderedDict()
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
        # Second dedup layer, on top of the id()-based one above -- see this
        # module's docstring for why the id()-based check alone isn't enough
        # across turns. Keyed on a hash of (conversation_id, every field of
        # the rendered line except ts/session/intent, which vary even for a
        # line that's a genuine repeat), LRU-bounded the same way as
        # _logged_system_prompt_hashes. system_prompt lines skip this layer
        # entirely -- they're already deduped (globally, not per-conversation
        # -- intentional, see that dedup's own comment) inside _render_message.
        self._logged_line_hashes: "OrderedDict[str, None]" = OrderedDict()

    async def write(
        self,
        intent_name: str,
        messages: list[dict[str, Any]],
        *,
        conversation_id: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        try:
            new_messages = self._new_messages_since_last_write(messages)
            if not new_messages:
                return

            lines: list[dict[str, Any]] = []
            for msg in new_messages:
                lines.extend(self._render_message(intent_name, msg))
            lines = [line for line in lines if not self._is_duplicate_line(line, conversation_id)]
            await self._write_lines(lines)
        except Exception as ex:
            # Debug logging must never break the actual conversation.
            logger.error(f"prompt log write failed: {ex}")

    async def write_usage(self, intent_name: str, usage: dict[str, Any]) -> None:
        """Log one turn's token-usage stats (e.g. Claude's
        ``cache_creation_input_tokens``/``cache_read_input_tokens``) as their
        own line in the same JSONL file, tagged with the same *intent_name*
        as the request line that produced them so the two can be correlated
        (and ``grep '"kind":"usage"'`` isolates just the cache-hit/-miss
        trend across turns without wading through message content)."""
        if not self.enabled or not usage:
            return
        try:
            ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            line = {
                "ts": ts,
                "session": self._session_id,
                "intent": intent_name,
                "role": "assistant",
                "kind": "usage",
                "usage": usage,
            }
            await self._write_lines([line])
        except Exception as ex:
            logger.error(f"prompt log usage write failed: {ex}")

    async def write_flag(
        self,
        intent_name: str,
        flag_kind: str,
        detail: dict[str, Any],
        *,
        conversation_id: str | None = None,
    ) -> None:
        """Log one line for a code-level guard finding (currently: the
        fabrication guard in ``unified.fabrication_guard``/``AgenticLoop`` --
        see those for what triggers this) -- e.g. a reply that claimed a
        tool-mediated action happened with no matching tool call that turn.
        Always writes (never deduped -- each occurrence is independently
        worth seeing), tagged with the same *intent_name* as the request/
        response lines around it so ``grep '"kind":"fabrication_flag"'``
        finds every instance directly, and *conversation_id* for the same
        reason ``write()`` takes it -- correlating flags back to one real
        conversation in a shared log file."""
        if not self.enabled:
            return
        try:
            ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            line = {
                "ts": ts,
                "session": self._session_id,
                "conversation_id": conversation_id,
                "intent": intent_name,
                "role": "assistant",
                "kind": flag_kind,
                **detail,
            }
            await self._write_lines([line])
        except Exception as ex:
            # Guard logging must never break the actual conversation.
            logger.error(f"prompt log flag write failed: {ex}")

    async def _write_lines(self, lines: list[dict[str, Any]]) -> None:
        if not lines:
            return
        text = "".join(json.dumps(line, default=str) + "\n" for line in lines)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(text)
        await self._maybe_prune()

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

    def _is_duplicate_line(self, line: dict[str, Any], conversation_id: str | None) -> bool:
        """True if this exact rendered line (scoped to *conversation_id*)
        has already been written -- see __init__'s comment on
        _logged_line_hashes for why this exists alongside the id()-based
        check in _new_messages_since_last_write."""
        if line.get("kind") == "system_prompt":
            return False  # already deduped inside _render_message.
        key_fields = {k: v for k, v in line.items() if k not in ("ts", "session", "intent")}
        digest = hashlib.sha256(
            json.dumps([conversation_id, key_fields], sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        if digest in self._logged_line_hashes:
            self._logged_line_hashes.move_to_end(digest)
            return True
        self._logged_line_hashes[digest] = None
        while len(self._logged_line_hashes) > _MAX_TRACKED_LINES:
            self._logged_line_hashes.popitem(last=False)
        return False

    def _render_message(self, intent_name: str, msg: dict[str, Any]) -> list[dict[str, Any]]:
        role = msg.get("role", "?")
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        base = {"ts": ts, "session": self._session_id, "intent": intent_name, "role": role}

        if role == "system":
            content = msg.get("content")
            text = content if isinstance(content, str) else json.dumps(content, default=str)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
            if digest in self._logged_system_prompt_hashes:
                self._logged_system_prompt_hashes.move_to_end(digest)
                return []
            self._logged_system_prompt_hashes[digest] = None
            while len(self._logged_system_prompt_hashes) > _MAX_TRACKED_SYSTEM_PROMPTS:
                self._logged_system_prompt_hashes.popitem(last=False)
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

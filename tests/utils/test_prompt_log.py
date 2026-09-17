"""``PromptLogManager`` -- the JSONL debug log of LLM interactions that
replaced the old hardcoded ``/tmp/nucore.prompt.md`` Python-``repr()`` dump.

Covers: real per-line JSON output shaped by content block (not by message),
the system-prompt hash dedupe (logged once per unique content, not once per
agentic-loop iteration), the id()-based "only the new tail" tracking that
lets write() be called with the *entire* accumulated messages list on every
call without re-logging what's already on disk, size-triggered pruning into
a dated gzip archive, and the two Gemini message shapes (``gemini_parts``
instead of ``content``, and ``content`` explicitly ``None``) that used to
crash the old ``_write_debug_prompt`` -- see git history for that bug.
"""

from __future__ import annotations

import gzip
import json

import pytest

from utils.prompt_log import PromptLogManager


def _lines(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


@pytest.mark.asyncio
async def test_disabled_manager_never_creates_a_file(tmp_path):
    manager = PromptLogManager(tmp_path / "logs", enabled=False)
    await manager.write("intent", [{"role": "user", "content": "hi"}])
    assert not (tmp_path / "logs").exists()


@pytest.mark.asyncio
async def test_enabled_by_default_and_creates_the_log_dir(tmp_path):
    log_dir = tmp_path / "logs"
    manager = PromptLogManager(log_dir)
    assert manager.enabled is True
    assert log_dir.exists()


@pytest.mark.asyncio
async def test_writes_one_json_line_per_content_block(tmp_path):
    manager = PromptLogManager(tmp_path)
    messages = [
        {"role": "system", "content": "you are an assistant"},
        {"role": "user", "content": "move it back"},
        {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "I'll move it"},
                {"type": "tool_use", "id": "toolu_1", "name": "node_op", "input": {"operation": "move"}},
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"status": "ok"}'}],
        },
    ]
    await manager.write("round 1", messages)
    lines = _lines(manager.path)

    kinds = [line["kind"] for line in lines]
    assert kinds == ["system_prompt", "text", "text", "tool_use", "tool_result"]

    tool_use = next(line for line in lines if line["kind"] == "tool_use")
    assert tool_use["tool"] == "node_op"
    assert tool_use["tool_use_id"] == "toolu_1"
    assert tool_use["input"] == {"operation": "move"}

    tool_result = next(line for line in lines if line["kind"] == "tool_result")
    assert tool_result["tool_use_id"] == "toolu_1"
    assert tool_result["content"] == {"status": "ok"}  # parsed, not a JSON string
    assert tool_result["role"] == "tool"  # not "user" -- fixes the old confusing label

    assert all("ts" in line and "session" in line for line in lines)


@pytest.mark.asyncio
async def test_identical_system_prompt_is_logged_once_across_separate_writes(tmp_path):
    manager = PromptLogManager(tmp_path)
    system = {"role": "system", "content": "same system prompt every time"}

    await manager.write("round 1", [system, {"role": "user", "content": "a"}])
    await manager.write("round 2", [system, {"role": "user", "content": "a"}])
    await manager.write("round 3", [system, {"role": "user", "content": "a"}])

    lines = _lines(manager.path)
    system_prompt_lines = [line for line in lines if line["kind"] == "system_prompt"]
    assert len(system_prompt_lines) == 1


@pytest.mark.asyncio
async def test_growing_message_list_only_logs_the_new_tail_each_call(tmp_path):
    # Mirrors AgenticLoop.run(): the same list object is extended in place
    # across iterations, and write() is called with the whole thing every
    # time -- only the newly-appended messages should hit disk each call.
    manager = PromptLogManager(tmp_path)
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}]

    await manager.write("round 1", messages)
    assert len(_lines(manager.path)) == 2

    messages.append({"role": "assistant", "content": "thinking"})
    await manager.write("round 2", messages)
    lines = _lines(manager.path)
    assert len(lines) == 3
    assert lines[-1]["kind"] == "text"
    assert lines[-1]["text"] == "thinking"

    messages.append({"role": "assistant", "content": "still going"})
    await manager.write("round 3", messages)
    assert len(_lines(manager.path)) == 4


@pytest.mark.asyncio
async def test_a_new_messages_list_is_not_treated_as_a_continuation(tmp_path):
    # A fresh turn builds a brand new messages list -- its system prompt is
    # a genuinely new object, but identical content still dedupes by hash.
    manager = PromptLogManager(tmp_path)
    system = {"role": "system", "content": "sys"}

    await manager.write("turn 1", [system, {"role": "user", "content": "first"}])
    await manager.write("turn 2", [dict(system), {"role": "user", "content": "second"}])

    lines = _lines(manager.path)
    assert [line["kind"] for line in lines] == ["system_prompt", "text", "text"]
    assert lines[1]["text"] == "first"
    assert lines[2]["text"] == "second"


@pytest.mark.asyncio
async def test_reconstructed_history_from_a_prior_turn_is_not_relogged(tmp_path):
    # Reproduces the real bug: UnifiedRuntime.handle_query rebuilds
    # history_messages from SessionStore from scratch every turn, so turn 2's
    # `messages` is a brand-new list object that happens to re-include turn
    # 1's user message verbatim (already on disk) alongside turn 1's
    # assistant reply (never actually written during turn 1 itself -- see
    # AgenticLoop.run, which now logs the final answer within its own turn;
    # this test still covers a caller that doesn't). Only genuinely new
    # content -- the reply (first time seen) and this turn's own new user
    # message -- should hit disk; the already-logged user message must not.
    manager = PromptLogManager(tmp_path)

    await manager.write("turn 1", [{"role": "user", "content": "q1"}], conversation_id="conv-1")
    await manager.write(
        "turn 2",
        [
            {"role": "user", "content": "q1"},
            {"role": "assistant", "content": "r1"},
            {"role": "user", "content": "q2"},
        ],
        conversation_id="conv-1",
    )

    lines = _lines(manager.path)
    texts = [(line["role"], line["text"]) for line in lines]
    assert texts == [("user", "q1"), ("assistant", "r1"), ("user", "q2")]


@pytest.mark.asyncio
async def test_duplicate_line_dedup_is_scoped_per_conversation_id(tmp_path):
    # The same literal reply genuinely happening in two different real
    # conversations must not suppress each other.
    manager = PromptLogManager(tmp_path)

    await manager.write("turn 1", [{"role": "assistant", "content": "Done."}], conversation_id="conv-a")
    await manager.write("turn 1", [{"role": "assistant", "content": "Done."}], conversation_id="conv-b")

    lines = _lines(manager.path)
    assert len(lines) == 2
    assert all(line["text"] == "Done." for line in lines)


@pytest.mark.asyncio
async def test_static_system_section_is_logged_once_while_the_tail_changes_per_turn(tmp_path):
    # Every turn sends [static, volatile tail]; the static section must
    # dedupe across turns even though a different system message (the tail)
    # was logged in between -- a single "last hash" would re-log it every turn.
    manager = PromptLogManager(tmp_path)
    static = {"role": "system", "content": "static rules + databases"}

    await manager.write("turn 1", [dict(static), {"role": "system", "content": "tail: 10:00"}, {"role": "user", "content": "a"}])
    await manager.write("turn 2", [dict(static), {"role": "system", "content": "tail: 10:01"}, {"role": "user", "content": "b"}])

    system_lines = [line["content"] for line in _lines(manager.path) if line["kind"] == "system_prompt"]
    assert system_lines == ["static rules + databases", "tail: 10:00", "tail: 10:01"]


@pytest.mark.asyncio
async def test_gemini_native_parts_fallback_does_not_crash(tmp_path):
    # Regression: GeminiAdapter.build_tool_round_trip_messages returns
    # messages shaped like {"role": ..., "gemini_parts": [...]} instead of a
    # plain "content" string for its tool-call round trip.
    manager = PromptLogManager(tmp_path)
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": None, "gemini_parts": [{"functionCall": {"name": "get_property", "args": {"id": "n1"}}}]},
        {"role": "tool", "content": None, "gemini_parts": [{"functionResponse": {"name": "get_property", "response": {"value": "on"}}}]},
    ]

    await manager.write("test intent", messages)

    lines = _lines(manager.path)
    tool_use = next(line for line in lines if line["kind"] == "tool_use")
    assert tool_use["tool"] == "get_property"
    assert tool_use["input"] == {"id": "n1"}

    tool_result = next(line for line in lines if line["kind"] == "tool_result")
    assert tool_result["tool"] == "get_property"
    assert tool_result["content"] == {"value": "on"}


@pytest.mark.asyncio
async def test_explicit_none_content_with_no_gemini_parts_does_not_crash(tmp_path):
    manager = PromptLogManager(tmp_path)
    await manager.write("test intent", [{"role": "tool", "content": None}])
    # No content and no gemini_parts -- nothing to log, but must not raise.
    assert _lines(manager.path) == []


@pytest.mark.asyncio
async def test_pruning_archives_oldest_lines_and_keeps_file_under_threshold(tmp_path):
    manager = PromptLogManager(tmp_path, max_bytes=2000)
    system = {"role": "system", "content": "sys " * 5}

    # Write enough distinct turns to push the active file past max_bytes.
    for i in range(60):
        messages = [dict(system), {"role": "user", "content": f"message number {i} " * 5}]
        await manager.write(f"round {i}", messages)

    assert manager.path.exists()
    assert manager.path.stat().st_size <= manager.max_bytes

    archives = list(tmp_path.glob("*.jsonl.gz"))
    assert len(archives) >= 1

    with gzip.open(archives[0], "rt", encoding="utf-8") as f:
        archived_lines = [json.loads(line) for line in f if line.strip()]
    assert len(archived_lines) > 0

    # The live file must still carry a system_prompt line for context even
    # though pruning happened.
    live_lines = _lines(manager.path)
    assert any(line["kind"] == "system_prompt" for line in live_lines)


@pytest.mark.asyncio
async def test_write_usage_logs_one_line_carrying_the_usage_dict(tmp_path):
    manager = PromptLogManager(tmp_path)
    usage = {"input_tokens": 120, "output_tokens": 40, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 14000}

    await manager.write_usage("unified (round 1)", usage)

    lines = _lines(manager.path)
    assert len(lines) == 1
    assert lines[0]["kind"] == "usage"
    assert lines[0]["intent"] == "unified (round 1)"
    assert lines[0]["usage"] == usage
    assert "ts" in lines[0] and "session" in lines[0]


@pytest.mark.asyncio
async def test_write_usage_is_a_noop_for_empty_usage(tmp_path):
    manager = PromptLogManager(tmp_path)
    await manager.write_usage("unified (round 1)", {})
    assert _lines(manager.path) == []


@pytest.mark.asyncio
async def test_disabled_manager_write_usage_never_creates_a_file(tmp_path):
    manager = PromptLogManager(tmp_path / "logs", enabled=False)
    await manager.write_usage("intent", {"input_tokens": 1})
    assert not (tmp_path / "logs").exists()


@pytest.mark.asyncio
async def test_write_failure_is_logged_not_raised(tmp_path, monkeypatch):
    # Debug logging must never break the actual conversation.
    manager = PromptLogManager(tmp_path)

    def _boom(*a, **kw):
        raise OSError("disk full")

    monkeypatch.setattr("builtins.open", _boom)
    await manager.write("intent", [{"role": "user", "content": "hi"}])  # must not raise

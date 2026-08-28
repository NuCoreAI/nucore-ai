"""maybe_compact_history -- triggers on estimated token size (not turn
count), collapses the oldest half of turns into one summary pseudo-turn via
a single LLM call, and falls back to plain truncation (never raises) when
that call fails.
"""

from __future__ import annotations

import pytest

from unified.history_compaction import (
    _COMPACTED_QUERY_LABEL,
    _MIN_TURNS_TO_COMPACT,
    estimate_tokens,
    maybe_compact_history,
)
from unified.models import ConversationHistory, ConversationTurn


class _FakeLLMClient:
    def __init__(
        self,
        *,
        text: str | None = "a summary",
        raises: Exception | None = None,
        response_key: str = "text",
    ):
        self.text = text
        self.raises = raises
        self.response_key = response_key
        self.calls: list[dict] = []

    async def generate(self, *, messages, config=None, tools=None, expect_json=False):
        self.calls.append({"messages": messages, "config": config, "tools": tools})
        if self.raises is not None:
            raise self.raises
        return {self.response_key: self.text}


def _history_with_turns(n: int, *, body: str = "x") -> ConversationHistory:
    history = ConversationHistory(max_turns=100)
    for i in range(n):
        history.turns.append(ConversationTurn(query=f"query {i} {body}", response=f"response {i} {body}"))
    return history


def _big_body(chars: int) -> str:
    return "x" * chars


@pytest.mark.asyncio
async def test_no_compaction_below_token_budget():
    history = _history_with_turns(_MIN_TURNS_TO_COMPACT + 2, body="short")
    original_turns = list(history.turns)
    llm = _FakeLLMClient()

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=10_000)

    assert compacted is False
    assert history.turns == original_turns
    assert llm.calls == []


@pytest.mark.asyncio
async def test_no_compaction_below_min_turns_even_over_budget():
    # Each turn is huge, but there aren't enough turns to bother compacting.
    history = _history_with_turns(_MIN_TURNS_TO_COMPACT - 1, body=_big_body(5000))
    original_turns = list(history.turns)
    llm = _FakeLLMClient()

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=100)

    assert compacted is False
    assert history.turns == original_turns
    assert llm.calls == []


@pytest.mark.asyncio
async def test_compaction_collapses_oldest_half_into_summary_turn():
    history = _history_with_turns(8, body=_big_body(2000))
    llm = _FakeLLMClient(text="the summary text")

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={"model": "m"}, token_budget=100)

    assert compacted is True
    # 8 turns -> split at 4: oldest 4 summarized into 1, newest 4 kept.
    assert len(history.turns) == 5
    assert history.turns[0].query == _COMPACTED_QUERY_LABEL
    assert history.turns[0].response == "the summary text"
    assert [t.query for t in history.turns[1:]] == [f"query {i} {_big_body(2000)}" for i in range(4, 8)]
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_compaction_reads_summary_from_content_key_for_non_claude_adapters():
    # Only ClaudeAdapter's generate() returns text under "text" -- OpenAI/Gemini
    # adapters return it under "content". Without the fallback, this would
    # silently summarize to "" and fall back to hard truncation instead.
    history = _history_with_turns(8, body=_big_body(2000))
    llm = _FakeLLMClient(text="the summary text", response_key="content")

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={"model": "m"}, token_budget=100)

    assert compacted is True
    assert history.turns[0].query == _COMPACTED_QUERY_LABEL
    assert history.turns[0].response == "the summary text"


@pytest.mark.asyncio
async def test_compaction_never_streams_the_internal_summarization_call():
    history = _history_with_turns(8, body=_big_body(2000))
    llm = _FakeLLMClient(text="summary")
    live_config = {"model": "m", "stream": True, "stream_handler": lambda chunk: None}

    await maybe_compact_history(history, llm_client=llm, llm_config=live_config, token_budget=100)

    sent_config = llm.calls[0]["config"]
    assert sent_config["stream"] is False
    assert "stream_handler" not in sent_config
    # The caller's own config dict must be untouched (no mutation-by-reference).
    assert live_config["stream"] is True
    assert "stream_handler" in live_config


@pytest.mark.asyncio
async def test_summarization_failure_falls_back_to_hard_truncation_without_raising():
    history = _history_with_turns(8, body=_big_body(2000))
    llm = _FakeLLMClient(raises=RuntimeError("provider unavailable"))

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=100)

    assert compacted is True
    assert len(history.turns) == 4
    assert all(t.query != _COMPACTED_QUERY_LABEL for t in history.turns)
    assert [t.query for t in history.turns] == [f"query {i} {_big_body(2000)}" for i in range(4, 8)]


@pytest.mark.asyncio
async def test_empty_summary_text_also_falls_back_to_truncation():
    history = _history_with_turns(8, body=_big_body(2000))
    llm = _FakeLLMClient(text="   ")  # blank after strip()

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=100)

    assert compacted is True
    assert len(history.turns) == 4
    assert all(t.query != _COMPACTED_QUERY_LABEL for t in history.turns)


@pytest.mark.asyncio
async def test_second_compaction_pass_resummarizes_the_pseudo_turn_with_newer_turns():
    history = _history_with_turns(8, body=_big_body(2000))
    llm = _FakeLLMClient(text="first summary")
    await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=100)
    assert history.turns[0].query == _COMPACTED_QUERY_LABEL

    # Grow the history again past the min-turns floor and re-trigger.
    for i in range(8, 13):
        history.turns.append(ConversationTurn(query=f"query {i} {_big_body(2000)}", response=f"response {i}"))

    llm.text = "second summary"
    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=100)

    assert compacted is True
    assert history.turns[0].query == _COMPACTED_QUERY_LABEL
    assert history.turns[0].response == "second summary"
    # The pseudo-turn from the first pass was itself folded into this summary.
    first_call_messages = llm.calls[1]["messages"]
    assert _COMPACTED_QUERY_LABEL in first_call_messages[0]["content"]


def test_estimate_tokens_is_roughly_chars_over_four():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("a" * 40) == 10

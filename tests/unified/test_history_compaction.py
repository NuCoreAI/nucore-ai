"""maybe_compact_history -- triggers on estimated token size (not turn
count), collapses the oldest half of turns into one summary pseudo-turn via
a single LLM call, and falls back to plain truncation (never raises) when
that call fails.
"""

from __future__ import annotations

import pytest

from unified.history_compaction import (
    _budget_notice,
    _COMPACTED_NOTICE,
    _COMPACTED_QUERY_LABEL,
    _MIN_TURNS_TO_COMPACT,
    _history_tokens,
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


def test_history_tokens_sizes_from_rich_messages_when_present():
    # A turn whose query/response text is tiny but whose rich .messages
    # transcript is large must be sized from that transcript, not just the
    # two short text fields -- otherwise compaction undercounts badly and
    # fires far too late relative to the real prompt size being sent.
    big_messages = [{"role": "user", "content": [{"type": "tool_result", "content": "x" * 8000}]}]
    turn = ConversationTurn(query="hi", response="ok", messages=big_messages)

    assert _history_tokens([turn]) > estimate_tokens("hi") + estimate_tokens("ok")
    assert _history_tokens([turn]) >= 1900  # ~8000 chars / 4, minus json overhead slack


def test_history_tokens_falls_back_to_query_response_when_messages_empty():
    turn = ConversationTurn(query="a" * 40, response="b" * 40)
    assert _history_tokens([turn]) == estimate_tokens(turn.query) + estimate_tokens(turn.response)


@pytest.mark.asyncio
async def test_compaction_collapses_summarized_turns_rich_messages_to_text_only():
    # Once a turn's rich transcript is folded into a summary, the
    # replacement pseudo-turn must not carry any summarized turn's
    # .messages forward -- compaction's whole point is a cheap textual
    # sketch of older context, not keeping raw transcripts alive forever.
    history = _history_with_turns(8, body=_big_body(2000))
    for turn in history.turns[:4]:
        turn.messages = [{"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "n", "input": {}}]}]
    llm = _FakeLLMClient(text="the summary text")

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={"model": "m"}, token_budget=100)

    assert compacted is True
    assert history.turns[0].query == _COMPACTED_QUERY_LABEL
    assert history.turns[0].messages == []


@pytest.mark.asyncio
async def test_compaction_preserves_the_kept_turns_rich_messages():
    # The newest half, which survives compaction untouched, must keep its
    # own rich transcript -- only the summarized-away older half collapses.
    history = _history_with_turns(8, body=_big_body(2000))
    rich = [{"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "n", "input": {}}]}]
    history.turns[-1].messages = rich
    llm = _FakeLLMClient(text="the summary text")

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={"model": "m"}, token_budget=100)

    assert compacted is True
    assert history.turns[-1].messages == rich


@pytest.mark.asyncio
async def test_summarization_prompt_ignores_rich_messages_and_uses_query_response_text():
    # The summarization call's own prompt must stay driven by the clean
    # query/response text, not balloon with a turn's raw tool-call JSON.
    history = _history_with_turns(8, body=_big_body(2000))
    history.turns[0].messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "n", "input": {"giant": "z" * 50000}}]}
    ]
    llm = _FakeLLMClient(text="summary")

    await maybe_compact_history(history, llm_client=llm, llm_config={"model": "m"}, token_budget=100)

    sent_prompt = llm.calls[0]["messages"][0]["content"]
    assert "giant" not in sent_prompt
    assert f"query 0 {_big_body(2000)}" in sent_prompt


# --- `notify`/`force` -- budget-threshold progress notices and the manual
# /compact-session path (UnifiedRuntime.compact_session) ---


@pytest.mark.asyncio
async def test_notify_fires_once_per_decile_and_is_capped_below_100_percent():
    history = _history_with_turns(_MIN_TURNS_TO_COMPACT, body=_big_body(200))
    total = _history_tokens(history.turns)
    llm = _FakeLLMClient()
    notices: list[str] = []

    async def notify(text):
        notices.append(text)

    budget_55_percent = total * 100 // 55
    compacted = await maybe_compact_history(
        history, llm_client=llm, llm_config={}, token_budget=budget_55_percent, notify=notify
    )
    assert compacted is False
    assert notices == [_budget_notice(50)]
    assert history.last_budget_notice_decile == 5

    # Same ~55% bracket again -- must not repeat.
    notices.clear()
    compacted = await maybe_compact_history(
        history, llm_client=llm, llm_config={}, token_budget=budget_55_percent, notify=notify
    )
    assert compacted is False
    assert notices == []

    # Jump straight to ~95% -- decile 9 (90%), never a generic "100%" line.
    notices.clear()
    budget_95_percent = total * 100 // 95
    compacted = await maybe_compact_history(
        history, llm_client=llm, llm_config={}, token_budget=budget_95_percent, notify=notify
    )
    assert compacted is False
    assert notices == [_budget_notice(90)]
    assert history.last_budget_notice_decile == 9


@pytest.mark.asyncio
async def test_notify_is_silent_below_50_percent():
    history = _history_with_turns(_MIN_TURNS_TO_COMPACT, body="short")
    notices: list[str] = []

    async def notify(text):
        notices.append(text)

    compacted = await maybe_compact_history(history, llm_client=_FakeLLMClient(), llm_config={}, token_budget=10_000, notify=notify)

    assert compacted is False
    assert notices == []
    assert history.last_budget_notice_decile == 0


@pytest.mark.asyncio
async def test_notify_gets_the_compacted_wording_when_compaction_actually_runs():
    history = _history_with_turns(8, body=_big_body(2000))
    history.last_budget_notice_decile = 9  # as if a prior turn already sent the 90% notice
    llm = _FakeLLMClient(text="summary")
    notices: list[str] = []

    async def notify(text):
        notices.append(text)

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=100, notify=notify)

    assert compacted is True
    assert notices == [_COMPACTED_NOTICE]
    assert history.last_budget_notice_decile == 0


@pytest.mark.asyncio
async def test_force_compacts_even_under_budget():
    history = _history_with_turns(_MIN_TURNS_TO_COMPACT, body="short")
    llm = _FakeLLMClient(text="forced summary")
    notices: list[str] = []

    async def notify(text):
        notices.append(text)

    compacted = await maybe_compact_history(
        history, llm_client=llm, llm_config={}, token_budget=10_000_000, force=True, notify=notify
    )

    assert compacted is True
    assert history.turns[0].response == "forced summary"
    assert notices == [_COMPACTED_NOTICE]


@pytest.mark.asyncio
async def test_force_still_requires_min_turns_to_compact():
    history = _history_with_turns(_MIN_TURNS_TO_COMPACT - 1, body="short")
    llm = _FakeLLMClient()

    compacted = await maybe_compact_history(
        history, llm_client=llm, llm_config={}, token_budget=10_000_000, force=True
    )

    assert compacted is False
    assert len(history.turns) == _MIN_TURNS_TO_COMPACT - 1

"""maybe_compact_history -- the only limit on replayed history (there is no
turn-count cap): triggers on estimated token size, keeps the newest turns
that fit in half the budget (hysteresis, so the provider's cached prefix
stays stable between compactions), collapses everything older into one
summary pseudo-turn via a single LLM call, and falls back to plain
truncation (never raises) when that call fails.
"""

from __future__ import annotations

import pytest

from unified import history_compaction
from unified.history_compaction import (
    _TOOL_ACTIVITY_CHARS_PER_TURN,
    _TOOL_RESULT_CHARS,
    _budget_notice,
    _render_turns_for_summary,
    _COMPACTED_NOTICE,
    _COMPACTED_QUERY_LABEL,
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
    history = ConversationHistory()
    for i in range(n):
        history.turns.append(ConversationTurn(query=f"query {i} {body}", response=f"response {i} {body}"))
    return history


def _big_body(chars: int) -> str:
    return "x" * chars


def _budget_keeping(history: ConversationHistory, kept: int) -> int:
    """A token budget the (uniformly sized) *history* is over, whose half-
    budget keep target fits exactly the newest *kept* turns."""
    per_turn = _history_tokens(history.turns[:1])
    return 2 * kept * per_turn + 1


@pytest.mark.asyncio
async def test_no_compaction_below_token_budget():
    history = _history_with_turns(8, body="short")
    original_turns = list(history.turns)
    llm = _FakeLLMClient()

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=10_000)

    assert compacted is False
    assert history.turns == original_turns
    assert llm.calls == []


@pytest.mark.asyncio
async def test_a_few_huge_turns_over_budget_still_compact():
    # No minimum-turn gate: three turns that together blow the budget (and
    # would blow a model's context window) must still be compacted.
    history = _history_with_turns(3, body=_big_body(5000))
    llm = _FakeLLMClient(text="the summary text")

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=100)

    assert compacted is True
    assert [t.query for t in history.turns] == [_COMPACTED_QUERY_LABEL, f"query 2 {_big_body(5000)}"]
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_compaction_keeps_newest_turns_fitting_half_the_budget_and_summarizes_the_rest():
    history = _history_with_turns(8, body=_big_body(2000))
    llm = _FakeLLMClient(text="the summary text")
    budget = _budget_keeping(history, 3)

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={"model": "m"}, token_budget=budget)

    assert compacted is True
    # Newest 3 fit in budget // 2: oldest 5 summarized into 1, newest 3 kept.
    assert len(history.turns) == 4
    assert history.turns[0].query == _COMPACTED_QUERY_LABEL
    assert history.turns[0].response == "the summary text"
    assert [t.query for t in history.turns[1:]] == [f"query {i} {_big_body(2000)}" for i in range(5, 8)]
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

    compacted = await maybe_compact_history(
        history, llm_client=llm, llm_config={}, token_budget=_budget_keeping(history, 3)
    )

    assert compacted is True
    assert len(history.turns) == 3
    assert all(t.query != _COMPACTED_QUERY_LABEL for t in history.turns)
    assert [t.query for t in history.turns] == [f"query {i} {_big_body(2000)}" for i in range(5, 8)]


@pytest.mark.asyncio
async def test_empty_summary_text_also_falls_back_to_truncation():
    history = _history_with_turns(8, body=_big_body(2000))
    llm = _FakeLLMClient(text="   ")  # blank after strip()

    compacted = await maybe_compact_history(
        history, llm_client=llm, llm_config={}, token_budget=_budget_keeping(history, 3)
    )

    assert compacted is True
    assert len(history.turns) == 3
    assert all(t.query != _COMPACTED_QUERY_LABEL for t in history.turns)


@pytest.mark.asyncio
async def test_second_compaction_pass_resummarizes_the_pseudo_turn_with_newer_turns():
    history = _history_with_turns(8, body=_big_body(2000))
    llm = _FakeLLMClient(text="first summary")
    await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=100)
    assert history.turns[0].query == _COMPACTED_QUERY_LABEL

    # Grow the history again past the budget and re-trigger.
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


def test_estimate_tokens_is_roughly_chars_over_three():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 1
    assert estimate_tokens("a" * 30) == 10


def test_history_tokens_sizes_from_rich_messages_when_present():
    # A turn whose query/response text is tiny but whose rich .messages
    # transcript is large must be sized from that transcript, not just the
    # two short text fields -- otherwise compaction undercounts badly and
    # fires far too late relative to the real prompt size being sent.
    big_messages = [{"role": "user", "content": [{"type": "tool_result", "content": "x" * 8000}]}]
    turn = ConversationTurn(query="hi", response="ok", messages=big_messages)

    assert _history_tokens([turn]) > estimate_tokens("hi") + estimate_tokens("ok")
    assert _history_tokens([turn]) >= 2600  # ~8000 chars / 3, minus json overhead slack


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
    # The newest turn(s), which survive compaction untouched, must keep its
    # own rich transcript -- only the summarized-away older turns collapse.
    history = _history_with_turns(8, body=_big_body(2000))
    rich = [{"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "n", "input": {}}]}]
    history.turns[-1].messages = rich
    llm = _FakeLLMClient(text="the summary text")

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={"model": "m"}, token_budget=100)

    assert compacted is True
    assert history.turns[-1].messages == rich


@pytest.mark.asyncio
async def test_summarization_prompt_stays_bounded_even_with_a_giant_tool_call():
    # The summary call sees each turn's tool calls, but only a short excerpt --
    # never a turn's raw tool-call JSON in full.
    history = _history_with_turns(8, body=_big_body(2000))
    history.turns[0].messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t", "name": "n", "input": {"giant": "z" * 50000}}]}
    ]
    llm = _FakeLLMClient(text="summary")

    await maybe_compact_history(history, llm_client=llm, llm_config={"model": "m"}, token_budget=100)

    sent_prompt = llm.calls[0]["messages"][0]["content"]
    assert "z" * 1000 not in sent_prompt
    assert f"query 0 {_big_body(2000)}" in sent_prompt


# --- `notify`/`force` -- budget-threshold progress notices and the manual
# /compact-session path (UnifiedRuntime.compact_session) ---


@pytest.mark.asyncio
async def test_notify_fires_once_per_decile_and_is_capped_below_100_percent():
    history = _history_with_turns(6, body=_big_body(200))
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
    history = _history_with_turns(6, body="short")
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
    history = _history_with_turns(6, body="short")
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
async def test_force_keeps_about_the_newest_half_by_size():
    history = _history_with_turns(6, body="short")
    llm = _FakeLLMClient(text="forced summary")

    await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=10_000_000, force=True)

    assert [t.query for t in history.turns[1:]] == [f"query {i} short" for i in (3, 4, 5)]


@pytest.mark.asyncio
async def test_force_with_only_one_turn_is_a_noop():
    history = _history_with_turns(1, body="short")
    llm = _FakeLLMClient()

    compacted = await maybe_compact_history(
        history, llm_client=llm, llm_config={}, token_budget=10_000_000, force=True
    )

    assert compacted is False
    assert len(history.turns) == 1
    assert llm.calls == []


# --- hysteresis, over-budget single turns, and the summary's own size cap ---


@pytest.mark.asyncio
async def test_compaction_does_not_refire_on_the_very_next_turn():
    history = _history_with_turns(9, body=_big_body(300))
    per_turn = _history_tokens(history.turns[:1])
    budget = 8 * per_turn  # 9 turns is over; the kept ~4 are well under
    llm = _FakeLLMClient(text="s")

    assert await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=budget) is True
    history.turns.append(ConversationTurn(query=f"query 9 {_big_body(300)}", response=f"response 9 {_big_body(300)}"))

    assert await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=budget) is False
    assert len(llm.calls) == 1


@pytest.mark.asyncio
async def test_history_stays_bounded_and_compaction_is_infrequent_over_a_long_session():
    history = ConversationHistory()
    llm = _FakeLLMClient(text="s")
    per_turn = _history_tokens([ConversationTurn(query=f"query 0 {_big_body(300)}", response=f"response 0 {_big_body(300)}")])
    budget = 10 * per_turn
    compaction_turns: list[int] = []

    for i in range(100):
        history.turns.append(
            ConversationTurn(query=f"query {i % 10} {_big_body(300)}", response=f"response {i % 10} {_big_body(300)}")
        )
        if await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=budget):
            compaction_turns.append(i)
        assert _history_tokens(history.turns) <= budget

    assert len(compaction_turns) >= 5
    # Half-budget hysteresis: never back-to-back, and far fewer than one per turn.
    assert all(b - a >= 3 for a, b in zip(compaction_turns, compaction_turns[1:]))
    assert len(compaction_turns) <= 100 // 3


@pytest.mark.asyncio
async def test_latest_turn_alone_over_budget_is_a_noop_with_a_warning(monkeypatch):
    history = _history_with_turns(1, body=_big_body(5000))
    llm = _FakeLLMClient()
    warnings: list[str] = []
    monkeypatch.setattr(history_compaction.logger, "warning", lambda msg, *a, **k: warnings.append(msg))

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=100)

    assert compacted is False
    assert llm.calls == []
    assert len(history.turns) == 1
    assert any("nothing older than the latest turn" in w for w in warnings)


@pytest.mark.asyncio
async def test_a_lone_summary_pseudo_turn_is_never_resummarized(monkeypatch):
    history = ConversationHistory()
    history.turns = [
        ConversationTurn(query=_COMPACTED_QUERY_LABEL, response="old summary"),
        ConversationTurn(query="q", response="r" * 5000),
    ]
    llm = _FakeLLMClient()
    monkeypatch.setattr(history_compaction.logger, "warning", lambda *a, **k: None)

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=100)

    assert compacted is False
    assert llm.calls == []
    assert history.turns[0].response == "old summary"


@pytest.mark.asyncio
async def test_compaction_leaving_an_over_budget_latest_turn_warns(monkeypatch):
    history = ConversationHistory()
    history.turns = [
        ConversationTurn(query="small", response="small"),
        ConversationTurn(query="huge", response="h" * 5000),
    ]
    llm = _FakeLLMClient(text="s")
    warnings: list[str] = []
    monkeypatch.setattr(history_compaction.logger, "warning", lambda msg, *a, **k: warnings.append(msg))

    compacted = await maybe_compact_history(history, llm_client=llm, llm_config={}, token_budget=100)

    assert compacted is True
    assert [t.query for t in history.turns] == [_COMPACTED_QUERY_LABEL, "huge"]
    assert any("still over budget after compaction" in w for w in warnings)


@pytest.mark.asyncio
async def test_summary_call_output_is_capped_below_the_profiles_generation_max_tokens():
    # A profile's max_tokens (e.g. 64000 for plugin_authoring) is sized for
    # generation; the summary must not be allowed to eat the budget it protects.
    history = _history_with_turns(8, body=_big_body(2000))
    llm = _FakeLLMClient(text="s")

    await maybe_compact_history(
        history, llm_client=llm, llm_config={"max_tokens": 64000}, token_budget=_budget_keeping(history, 3)
    )

    sent = llm.calls[0]
    assert sent["config"]["max_tokens"] == max(512, _budget_keeping(_history_with_turns(8, body=_big_body(2000)), 3) // 4)
    assert "words" in sent["messages"][0]["content"]


@pytest.mark.asyncio
async def test_summary_call_respects_a_smaller_configured_max_tokens():
    history = _history_with_turns(8, body=_big_body(2000))
    llm = _FakeLLMClient(text="s")

    await maybe_compact_history(
        history, llm_client=llm, llm_config={"max_tokens": 300}, token_budget=_budget_keeping(history, 3)
    )

    assert llm.calls[0]["config"]["max_tokens"] == 300


# --- the summarizer sees which tools ran and short excerpts of what they returned ---


def _tool_turn(messages) -> ConversationTurn:
    return ConversationTurn(query="q", response="r", messages=messages)


def test_summary_input_lists_claude_tool_calls_with_result_excerpts():
    turn = _tool_turn(
        [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "list_generated_plugins", "input": {}}]},
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": "a", "content": [{"type": "text", "text": 'folders: ["lg_webos", "midas_rates"]'}]}
                ],
            },
        ]
    )

    rendered = _render_turns_for_summary([turn])

    assert "list_generated_plugins({})" in rendered
    assert '-> folders: ["lg_webos", "midas_rates"]' in rendered


def test_summary_input_excerpts_are_truncated_and_multi_line_results_flattened():
    turn = _tool_turn(
        [
            {"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "read_file", "input": {"path": "x.py"}}]},
            {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "line1\nline2 " + "y" * 5000}]},
        ]
    )

    rendered = _render_turns_for_summary([turn])

    assert "line1 line2" in rendered
    assert "y" * (_TOOL_RESULT_CHARS + 50) not in rendered
    assert "chars in all]" in rendered  # tells the summarizer the excerpt is partial
    assert rendered.count("\n") == 3  # User, Assistant, header, one tool line -- nothing multi-line


def test_summary_input_handles_openai_chat_and_responses_shapes():
    chat = _tool_turn(
        [
            {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "function": {"name": "get_time_info", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "it is noon"},
        ]
    )
    responses = _tool_turn(
        [
            {"type": "function_call", "call_id": "k", "name": "list_variables", "arguments": "{}"},
            {"type": "function_call_output", "call_id": "k", "output": "3 variables"},
        ]
    )

    rendered = _render_turns_for_summary([chat, responses])

    assert "get_time_info({}) -> it is noon" in rendered
    assert "list_variables({}) -> 3 variables" in rendered


def test_summary_input_shows_only_the_query_of_a_native_web_search():
    turn = _tool_turn(
        [{"role": "assistant", "content": [{"type": "server_tool_use", "id": "s", "name": "web_search", "input": {"query": "eero api"}}]}]
    )

    assert 'web_search({"query":"eero api"})' in _render_turns_for_summary([turn])


def test_summary_input_caps_tool_activity_per_turn():
    blocks = [{"type": "tool_use", "id": str(i), "name": "t", "input": {"i": "x" * 100}} for i in range(100)]
    turn = _tool_turn([{"role": "assistant", "content": blocks}])

    rendered = _render_turns_for_summary([turn])

    assert len(rendered) < _TOOL_ACTIVITY_CHARS_PER_TURN + 300
    assert "more tool call(s) omitted" in rendered


def test_summary_input_for_a_turn_without_a_rich_transcript_is_unchanged():
    turn = ConversationTurn(query="q", response="r")

    assert _render_turns_for_summary([turn]) == "User: q\nAssistant: r"


# --- forked summarization: the whole live conversation becomes one summary ---


class _Fork:
    def __init__(self, text="the fork summary", raises=None):
        self.text = text
        self.raises = raises
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, instruction, max_tokens):
        self.calls.append((instruction, max_tokens))
        if self.raises is not None:
            raise self.raises
        return self.text


@pytest.mark.asyncio
async def test_fork_summary_replaces_the_entire_history_and_skips_the_standalone_summarizer():
    history = _history_with_turns(8, body=_big_body(2000))
    llm = _FakeLLMClient()
    fork = _Fork()
    notices: list[str] = []

    async def notify(text):
        notices.append(text)

    history.last_budget_notice_decile = 9
    compacted = await maybe_compact_history(
        history, llm_client=llm, llm_config={}, token_budget=100, notify=notify, fork_summarizer=fork
    )

    assert compacted is True
    assert [(t.query, t.response, t.messages) for t in history.turns] == [(_COMPACTED_QUERY_LABEL, "the fork summary", [])]
    assert llm.calls == []  # the blind summarizer never ran
    assert len(fork.calls) == 1
    instruction, max_tokens = fork.calls[0]
    assert "do not call any tools" in instruction and "words" in instruction
    assert max_tokens == max(512, 100 // 4)
    assert notices == [_COMPACTED_NOTICE]
    assert history.last_budget_notice_decile == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fork", [_Fork(raises=RuntimeError("400")), _Fork(text="   ")])
async def test_a_failed_or_empty_fork_falls_back_to_the_standalone_summarizer(fork):
    history = _history_with_turns(8, body=_big_body(2000))
    llm = _FakeLLMClient(text="blind summary")

    compacted = await maybe_compact_history(
        history,
        llm_client=llm,
        llm_config={},
        token_budget=_budget_keeping(history, 3),
        fork_summarizer=fork,
    )

    assert compacted is True
    assert len(fork.calls) == 1 and len(llm.calls) == 1
    assert history.turns[0].response == "blind summary"
    assert len(history.turns) == 4  # keep-tail path: summary + newest 3


@pytest.mark.asyncio
async def test_fork_is_not_attempted_for_a_single_turn_or_when_under_budget():
    one = _history_with_turns(1, body=_big_body(5000))
    fork = _Fork()
    assert await maybe_compact_history(one, llm_client=_FakeLLMClient(), llm_config={}, token_budget=100, fork_summarizer=fork) is False

    small = _history_with_turns(4, body="short")
    assert await maybe_compact_history(small, llm_client=_FakeLLMClient(), llm_config={}, token_budget=10_000, fork_summarizer=fork) is False

    assert fork.calls == []


@pytest.mark.asyncio
async def test_force_uses_the_fork_even_under_budget():
    history = _history_with_turns(3, body="short")
    fork = _Fork(text="forced fork summary")

    compacted = await maybe_compact_history(
        history, llm_client=_FakeLLMClient(), llm_config={}, token_budget=10_000_000, force=True, fork_summarizer=fork
    )

    assert compacted is True
    assert [t.response for t in history.turns] == ["forced fork summary"]


def test_notices_end_with_a_blank_line_so_the_reply_does_not_fuse_onto_them():
    assert _budget_notice(60).endswith("*\n\n")
    assert _COMPACTED_NOTICE.endswith("*\n\n")


@pytest.mark.asyncio
async def test_compactions_are_recorded_in_the_sessions_usage_stats():
    history = _history_with_turns(8, body=_big_body(2000))
    await maybe_compact_history(
        history, llm_client=_FakeLLMClient(), llm_config={}, token_budget=100, fork_summarizer=_Fork()
    )
    event = history.usage["compactions"][-1]
    assert event["kind"] == "fork" and event["before"] > event["after"]

    history = _history_with_turns(8, body=_big_body(2000))
    await maybe_compact_history(history, llm_client=_FakeLLMClient(), llm_config={}, token_budget=100)
    assert history.usage["compactions"][-1]["kind"] == "summary"

"""usage_stats -- the accounting and report behind /session-stats."""

from __future__ import annotations

from unified import usage_stats as us
from unified.models import ConversationHistory, ConversationTurn
from unified.session_persistence import history_from_dict, history_to_dict


def test_normalize_claude_usage_splits_cache_writes_by_ttl():
    rec = us.normalize_usage(
        {
            "input_tokens": 4,
            "cache_read_input_tokens": 1000,
            "cache_creation_input_tokens": 300,
            "cache_creation": {"ephemeral_1h_input_tokens": 200, "ephemeral_5m_input_tokens": 100},
            "output_tokens": 50,
        }
    )
    assert rec == {"input_total": 1304, "cache_read": 1000, "cache_write_5m": 100, "cache_write_1h": 200, "output": 50}


def test_normalize_claude_usage_without_a_ttl_breakdown_counts_writes_as_1h():
    rec = us.normalize_usage({"input_tokens": 2, "cache_creation_input_tokens": 70, "cache_read_input_tokens": 0})
    assert rec["cache_write_1h"] == 70 and rec["cache_write_5m"] == 0


def test_normalize_openai_chat_usage():
    rec = us.normalize_usage(
        {"prompt_tokens": 900, "completion_tokens": 40, "prompt_tokens_details": {"cached_tokens": 600}}
    )
    assert rec["input_total"] == 900 and rec["cache_read"] == 600 and rec["output"] == 40


def test_normalize_openai_responses_usage():
    rec = us.normalize_usage({"input_tokens": 800, "output_tokens": 30, "input_tokens_details": {"cached_tokens": 500}})
    assert rec["input_total"] == 800 and rec["cache_read"] == 500 and rec["output"] == 30


def test_normalize_gemini_usage_counts_thinking_as_output():
    rec = us.normalize_usage(
        {"promptTokenCount": 700, "cachedContentTokenCount": 400, "candidatesTokenCount": 20, "thoughtsTokenCount": 5}
    )
    assert rec["input_total"] == 700 and rec["cache_read"] == 400 and rec["output"] == 25


def test_normalize_unknown_shapes_give_zeros_and_never_raise():
    zero = {"input_total": 0, "cache_read": 0, "cache_write_5m": 0, "cache_write_1h": 0, "output": 0}
    assert us.normalize_usage(None) == zero
    assert us.normalize_usage({}) == zero
    assert us.normalize_usage({"input_tokens": "lots"}) == zero


def test_cost_is_priced_for_known_models_only():
    rec = {"input_total": 1_000_000, "cache_read": 500_000, "cache_write_5m": 0, "cache_write_1h": 100_000, "output": 10_000}
    # 400K fresh @2 + 500K read @0.2 + 100K 1h write @4 + 10K out @10
    assert round(us.cost_usd("claude-sonnet-5", rec), 4) == round((0.8 + 0.1 + 0.4 + 0.1), 4)
    assert us.cost_usd("some-other-model", rec) is None
    assert us.cost_usd(None, rec) is None


def test_context_window_known_unknown_and_override():
    assert us.context_window("claude-sonnet-5") == 1_000_000
    assert us.context_window("mystery-model") is None
    assert us.context_window("mystery-model", 64_000) == 64_000
    assert us.context_window("claude-sonnet-5", 0) == 1_000_000


def test_record_request_accumulates_totals_and_keeps_only_recent_rows():
    usage: dict = {}
    for _ in range(25):
        us.record_request(usage, "round", {"input_tokens": 1, "cache_read_input_tokens": 9, "output_tokens": 2}, "claude-sonnet-5")
    assert usage["requests"] == 25 and usage["input_total"] == 250 and usage["output"] == 50
    assert len(usage["recent"]) == 20
    assert usage["usd"] > 0 and "unpriced_requests" not in usage


def test_record_request_counts_unpriced_models_instead_of_guessing_a_cost():
    usage: dict = {}
    us.record_request(usage, "round", {"prompt_tokens": 10, "completion_tokens": 1}, "gpt-x")
    assert usage["unpriced_requests"] == 1 and "usd" not in usage


def test_record_helpers_never_raise_on_a_corrupt_usage_dict():
    bad = {"recent": "nope", "compactions": 7}
    us.record_request(bad, "round", {"input_tokens": 1})
    us.record_compaction(bad, "fork", 10, 2)


def test_usage_survives_session_persistence_and_a_bad_value_is_ignored():
    history = ConversationHistory()
    us.record_request(history.usage, "round", {"input_tokens": 3, "output_tokens": 4})
    restored = history_from_dict(history_to_dict(history))
    assert restored.usage["requests"] == 1 and restored.usage["output"] == 4
    assert history_from_dict({"turns": [], "usage": "garbage"}).usage == {}


def _turn(query, response, messages=None):
    return ConversationTurn(query=query, response=response, messages=messages or [])


def test_estimate_composition_separates_sections_tools_and_history_kinds():
    turns = [
        _turn("[compacted]", "s" * 300),
        _turn("q", "a", [{"role": "user", "content": "x" * 300}]),
        _turn(
            "q2",
            "a2",
            [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "1", "name": "t", "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "1", "content": "y" * 600}]},
            ],
        ),
    ]
    parts = dict(
        us.estimate_composition(["DEVICE DATABASE " + "z" * 300, "tail"], [{"name": "t"}], turns, "[compacted]")
    )
    assert any(label.startswith("system section 1 (DEVICE DATABASE)") for label in parts)
    assert parts["tools (1)"] > 0
    assert parts["history: compacted summary"] == 100
    assert parts["history: conversation text"] >= 100
    assert parts["history: tool calls and results"] >= 200


def _report(usage, composition=(("system section 1", 3000), ("tools (2)", 6000), ("history: conversation text", 1000)), **kw):
    args = dict(history_tokens=1000, history_budget=2000, window=1_000_000, max_tokens=8000, model="claude-sonnet-5")
    args.update(kw)
    return us.format_report(usage, list(composition), **args)


def test_report_calibrates_components_to_the_last_requests_real_input():
    usage: dict = {}
    us.record_request(usage, "round", {"input_tokens": 2, "cache_read_input_tokens": 19998, "output_tokens": 5}, "claude-sonnet-5")
    report = _report(usage)
    assert "calibrated" in report
    # 10,000 estimated -> 20,000 real: ratio 2.0 is the edge of the accepted range
    assert "| tools (2) | 12,000 | 60% |" in report
    assert "**Last request:** 20,000 in" in report


def test_report_stays_an_estimate_when_a_compaction_followed_the_last_request():
    usage: dict = {}
    us.record_request(usage, "round", {"input_tokens": 10000, "output_tokens": 5})
    usage["compactions"] = [{"ts": "9999-01-01T00:00:00Z", "kind": "fork", "before": 90000, "after": 5000}]
    report = _report(usage)
    assert "estimated" in report and "calibrated" not in report
    assert "**Compactions:** 1 recorded; latest (fork)" in report


def test_report_on_a_fresh_session_has_no_totals_and_shows_the_budget_share():
    report = _report({})
    assert "no requests recorded" in report
    assert "~1,000 of its 2,000-token budget (50%)" in report
    assert "of 1,000,000" in report


def test_report_without_a_known_window_omits_the_window_share():
    assert "of 1,000,000" not in _report({}, window=None)


def test_report_mentions_mid_turn_events():
    usage: dict = {}
    us.record_mid_turn_over_budget(usage, 7, 250_000, 240_000)
    assert "Mid-turn over-budget events:** 1 (latest at round 7" in _report(usage)

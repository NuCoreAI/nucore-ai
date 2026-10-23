"""ConversationHistory.append -- rolling-window eviction by turn count."""

from __future__ import annotations

from unified.models import ConversationHistory, ConversationTurn


def test_append_keeps_turns_within_max_turns_window():
    history = ConversationHistory(max_turns=3)

    for i in range(5):
        history.append(f"query {i}", f"response {i}")

    assert [t.query for t in history.turns] == ["query 2", "query 3", "query 4"]
    assert [t.response for t in history.turns] == ["response 2", "response 3", "response 4"]


def test_append_does_not_evict_while_under_the_window():
    history = ConversationHistory(max_turns=3)

    history.append("q1", "r1")
    history.append("q2", "r2")

    assert len(history.turns) == 2


def test_conversation_turn_defaults_messages_and_provider():
    turn = ConversationTurn(query="q", response="r")
    assert turn.messages == []
    assert turn.provider is None


def test_append_defaults_messages_and_provider_when_omitted():
    history = ConversationHistory(max_turns=3)
    history.append("q", "r")
    assert history.turns[-1].messages == []
    assert history.turns[-1].provider is None


def test_append_stores_rich_messages_and_provider_when_given():
    rich = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "file contents"}]},
    ]
    history = ConversationHistory(max_turns=3)
    history.append("q", "r", rich, provider="claude")
    assert history.turns[-1].messages == rich
    assert history.turns[-1].provider == "claude"


def test_append_does_not_alias_the_caller_supplied_messages_list():
    rich = [{"role": "assistant", "content": "x"}]
    history = ConversationHistory(max_turns=3)
    history.append("q", "r", rich)
    rich.append({"role": "user", "content": "mutated after the fact"})
    assert len(history.turns[-1].messages) == 1

"""ConversationHistory.append -- never evicts by count (token compaction is the only limit)."""

from __future__ import annotations

from unified.models import ConversationHistory, ConversationTurn


def test_append_never_evicts_turns():
    history = ConversationHistory()

    for i in range(50):
        history.append(f"query {i}", f"response {i}")

    assert len(history.turns) == 50
    assert history.turns[0].query == "query 0"
    assert history.turns[-1].response == "response 49"


def test_conversation_turn_defaults_messages_and_provider():
    turn = ConversationTurn(query="q", response="r")
    assert turn.messages == []
    assert turn.provider is None


def test_append_defaults_messages_and_provider_when_omitted():
    history = ConversationHistory()
    history.append("q", "r")
    assert history.turns[-1].messages == []
    assert history.turns[-1].provider is None


def test_append_stores_rich_messages_and_provider_when_given():
    rich = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "read_file", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "file contents"}]},
    ]
    history = ConversationHistory()
    history.append("q", "r", rich, provider="claude")
    assert history.turns[-1].messages == rich
    assert history.turns[-1].provider == "claude"


def test_append_does_not_alias_the_caller_supplied_messages_list():
    rich = [{"role": "assistant", "content": "x"}]
    history = ConversationHistory()
    history.append("q", "r", rich)
    rich.append({"role": "user", "content": "mutated after the fact"})
    assert len(history.turns[-1].messages) == 1

"""ConversationHistory.append -- rolling-window eviction by turn count."""

from __future__ import annotations

from unified.models import ConversationHistory


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

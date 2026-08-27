"""Token-based compaction for :class:`~models.ConversationHistory`.

``ConversationHistory.append`` already caps *turn count* (oldest turn
dropped once ``max_turns`` is exceeded), but says nothing about how large
those turns are -- a handful of diagnostic-dump turns can bloat the prompt
built into every subsequent request long before the turn-count cap bites.
``maybe_compact_history`` triggers on estimated *token size* instead, and
collapses the oldest half of the turns into one summary pseudo-turn (via a
single LLM call) rather than silently dropping them.
"""

from __future__ import annotations

from typing import Any

from .adapters import LLMAdapter
from .models import ConversationHistory, ConversationTurn
from utils import get_logger

logger = get_logger(__name__)

_DEFAULT_TOKEN_BUDGET = 20000
_MIN_TURNS_TO_COMPACT = 6
_COMPACTED_QUERY_LABEL = "[earlier conversation, summarized]"

_SUMMARY_PROMPT_HEADER = (
    "Summarize the conversation turns below concisely, in plain prose (no headers/bullets "
    "needed unless they help). Preserve concrete facts a later turn might still depend on -- "
    "device/routine names and ids, decisions made, values reported by tools -- and omit "
    "meta-commentary about the summarization itself. Do not fabricate anything not present "
    "below.\n\n"
)


def estimate_tokens(text: str) -> int:
    """Rough token-count estimate (~4 chars/token) -- no tokenizer dependency
    in this codebase to call instead."""
    return len(text or "") // 4


def _history_tokens(turns: list[ConversationTurn]) -> int:
    return sum(estimate_tokens(t.query) + estimate_tokens(t.response) for t in turns)


def _render_turns_for_summary(turns: list[ConversationTurn]) -> str:
    parts = []
    for turn in turns:
        parts.append(f"User: {turn.query}\nAssistant: {turn.response}")
    return "\n\n".join(parts)


async def _summarize_turns(
    turns: list[ConversationTurn], *, llm_client: LLMAdapter, llm_config: dict[str, Any]
) -> str:
    prompt = _SUMMARY_PROMPT_HEADER + _render_turns_for_summary(turns)
    # Never stream this internal call into whatever live handler the caller's
    # config carries -- that handler is wired to the user-facing reply, not
    # this behind-the-scenes summarization call.
    summary_config = dict(llm_config)
    summary_config.pop("stream_handler", None)
    summary_config["stream"] = False
    response = await llm_client.generate(messages=[{"role": "user", "content": prompt}], config=summary_config, tools=None)
    return (response or {}).get("text", "").strip()


async def maybe_compact_history(
    history: ConversationHistory,
    *,
    llm_client: LLMAdapter,
    llm_config: dict[str, Any],
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
) -> bool:
    """Collapse the oldest half of ``history.turns`` into one summary
    pseudo-turn when the history's estimated token size exceeds
    ``token_budget``. Mutates ``history.turns`` in place.

    No-ops when there aren't enough turns to meaningfully compact, or the
    history is already under budget. Never raises -- a failed summarization
    call falls back to plain hard truncation of the oldest half rather than
    blocking the user's actual turn.

    Returns:
        True iff compaction (summarized or fallback-truncated) happened.
    """
    turns = history.turns
    if len(turns) < _MIN_TURNS_TO_COMPACT:
        return False

    before_tokens = _history_tokens(turns)
    if before_tokens <= token_budget:
        return False

    split = len(turns) // 2
    to_summarize = turns[:split]
    keep = turns[split:]

    try:
        summary_text = await _summarize_turns(to_summarize, llm_client=llm_client, llm_config=llm_config)
        if not summary_text:
            raise ValueError("empty summary")
        history.turns = [ConversationTurn(query=_COMPACTED_QUERY_LABEL, response=summary_text)] + keep
        logger.info(
            f"Compacted {len(to_summarize)} conversation turns into a summary "
            f"(~{before_tokens} -> ~{_history_tokens(history.turns)} estimated tokens)"
        )
    except Exception as exc:
        history.turns = keep
        logger.warning(
            f"History summarization failed ({exc!r}) -- fell back to dropping the oldest "
            f"{len(to_summarize)} turns without a summary "
            f"(~{before_tokens} -> ~{_history_tokens(history.turns)} estimated tokens)"
        )

    return True

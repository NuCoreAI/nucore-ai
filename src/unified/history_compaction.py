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

import json
from typing import Any, Awaitable, Callable

from .adapters import LLMAdapter
from .models import ConversationHistory, ConversationTurn
from utils import get_logger

logger = get_logger(__name__)

_DEFAULT_TOKEN_BUDGET = 20000
_MIN_TURNS_TO_COMPACT = 6
_COMPACTED_QUERY_LABEL = "[earlier conversation, summarized]"

# Lowest token-budget decile (5 == 50%) that ever triggers a `notify` call --
# see maybe_compact_history's own docstring.
_MIN_BUDGET_NOTICE_DECILE = 5

_COMPACTED_NOTICE = (
    "*Session history was over budget, so I compacted the older turns. You can also do "
    "this any time with /compact-session.*"
)


def _budget_notice(percent: int) -> str:
    return f"*Session history is at {percent}% of its budget -- you can compact it any time with /compact-session.*"

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


def _turn_tokens(turn: ConversationTurn) -> int:
    """Estimate one turn's token footprint for the compaction budget check.

    A turn with a non-empty rich ``.messages`` transcript (every tool_use/
    tool_result round trip this turn produced -- see models.py's
    ConversationTurn.messages) is sized from that transcript's full
    JSON-serialized bytes, not just ``.query``/``.response`` -- those two
    short text fields would badly undercount a turn that made several tool
    calls (e.g. a multi-KB file read echoed back verbatim in a tool_result
    block), letting compaction fire far later than the actual prompt size
    sent to the model on every subsequent turn would justify.

    Falls back to ``.query``/``.response`` alone -- today's exact estimate
    -- for a turn with no rich transcript: pre-feature turns, the
    ``_COMPACTED_QUERY_LABEL`` summary pseudo-turn, and every turn whose
    messages a caller never captured. Never raises -- a serialization
    failure degrades to the text-only estimate rather than propagating.
    """
    if turn.messages:
        try:
            return estimate_tokens(json.dumps(turn.messages, ensure_ascii=False, default=str))
        except Exception:
            pass
    return estimate_tokens(turn.query) + estimate_tokens(turn.response)


def _history_tokens(turns: list[ConversationTurn]) -> int:
    return sum(_turn_tokens(t) for t in turns)


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
    response = response or {}
    return (response.get("text") or response.get("content") or "").strip()


async def maybe_compact_history(
    history: ConversationHistory,
    *,
    llm_client: LLMAdapter,
    llm_config: dict[str, Any],
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
    notify: Callable[[str], Awaitable[None]] | None = None,
    force: bool = False,
) -> bool:
    """Collapse the oldest half of ``history.turns`` into one summary
    pseudo-turn when the history's estimated token size exceeds
    ``token_budget``. Mutates ``history.turns`` in place.

    No-ops when there aren't enough turns to meaningfully compact (even with
    *force*), or (without *force*) the history is already under budget.
    Never raises -- a failed summarization call falls back to plain hard
    truncation of the oldest half rather than blocking the user's actual
    turn.

    *notify*, when given, is awaited with one italic Markdown status line
    whenever estimated usage crosses a new 10%-of-budget decile at or above
    50% (tracked on ``history.last_budget_notice_decile`` so the same
    decile isn't repeated every turn), or once compaction actually runs
    (superseding the generic decile wording with "compaction just
    happened" instead, for the same turn that crosses 100%). Reset to 0
    after compaction, so a later climb re-notifies from 50% again.
    Best-effort: an exception here is logged and swallowed, same contract
    as every other hook in this codebase (see AgenticLoop's
    on_raw_response/on_round_start).

    *force*, when True, skips the token-budget gate entirely (still
    requires ``_MIN_TURNS_TO_COMPACT`` turns) -- the manual
    ``/compact-session`` command's path (see
    ``UnifiedRuntime.compact_session``), letting a customer compact early
    even while under budget. *notify* isn't meaningful there -- that
    caller composes its own reply -- and should be left ``None``.

    Returns:
        True iff compaction (summarized or fallback-truncated) happened.
    """
    turns = history.turns
    if len(turns) < _MIN_TURNS_TO_COMPACT:
        return False

    before_tokens = _history_tokens(turns)
    over_budget = before_tokens > token_budget
    if not force and not over_budget:
        if notify is not None and token_budget > 0:
            decile = int(before_tokens * 10 // token_budget)
            # Capped at 9 (<100%) -- decile 10+ only ever gets the
            # "compaction just happened" wording below, never this generic
            # percentage one, even in the razor-thin edge case where
            # before_tokens lands exactly on token_budget (decile == 10 but
            # not, by this function's own `> token_budget` rule, "over
            # budget" yet).
            if _MIN_BUDGET_NOTICE_DECILE <= decile <= 9 and decile > history.last_budget_notice_decile:
                history.last_budget_notice_decile = decile
                try:
                    await notify(_budget_notice(decile * 10))
                except Exception as exc:
                    logger.error("unified: history-budget notify hook failed: %s", exc)
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

    history.last_budget_notice_decile = 0
    if notify is not None:
        try:
            await notify(_COMPACTED_NOTICE)
        except Exception as exc:
            logger.error("unified: history-budget notify hook failed: %s", exc)

    return True
